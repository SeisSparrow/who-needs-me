import json
from collections import Counter

import pytest

from tests.test_rules import IDLE_DONE, claude_screen
from who_needs_me import decide as dec
from who_needs_me.policy import decide, needs_model
from who_needs_me.rules import evaluate
from who_needs_me.split import dedupe, split

POL = {
    "tripwire_mode": "escalate",
    "wants_threshold": 0.2,
    "turn_end_min_confidence": 0.5,
    "severity": ["delete", "deploy", "spend", "machine"],
}


def answers(choice="reporting_done", conf=0.9, **wants):
    a = {
        "turn_end": {
            "type": "choice",
            "choice": choice,
            "confidence": conf,
            "probabilities": {choice: conf},
        }
    }
    for r in ("deploy", "spend", "delete", "machine"):
        p = wants.get(r, 0.01)
        a[f"wants_{r}"] = {"type": "noul", "noul": p, "confidence": max(p, 1 - p)}
    return a


# ---- policy: labeling conventions


@pytest.mark.parametrize(
    ("choice", "state"),
    [
        ("reporting_done", "idle_done"),
        ("waiting_on_background", "idle_done"),  # convention 1
        ("asking_question", "waiting_reply"),
        ("awaiting_approval", "waiting_reply"),  # approval of something not in wants_*
    ],
)
def test_turn_end_mapping(choice, state):
    d = decide(evaluate(IDLE_DONE, "claude"), answers(choice), POL)
    assert (d.state, d.decided_by) == (state, "model")


def test_wants_threshold_and_severity():
    l0 = evaluate(IDLE_DONE, "claude")
    d = decide(l0, answers(deploy=0.6, spend=0.9, delete=0.25), POL)
    assert (d.state, d.human_reason) == ("needs_human", "delete")  # delete > deploy > spend
    assert decide(l0, answers(deploy=0.19), POL).state == "idle_done"


def test_model_decision_evidence_is_the_relevant_line():
    body = [
        "● The plan is ready.",
        "  The release waits for your go-ahead.",
        "  More notes.",
        "  Other text.",
        "  Final words.",
    ]
    l0 = evaluate(claude_screen(body), "claude")
    hint = {**POL, "tripwire_mode": "hint"}  # so the model, not the tripwire, decides
    d = decide(l0, answers("awaiting_approval", deploy=0.95), hint)
    assert (d.decided_by, d.evidence) == ("model", "The release waits for your go-ahead.")


def test_low_confidence_and_unclear_escalate():
    l0 = evaluate(IDLE_DONE, "claude")
    assert decide(l0, answers(conf=0.4), POL).human_reason == "low_confidence"
    assert decide(l0, answers("unclear", 0.9), POL).human_reason == "low_confidence"


def test_tripwire_escalate_vs_hint():
    l0 = evaluate(claude_screen(["● Ready. Approve the deploy of v2?"]), "claude")
    d = decide(l0, None, POL)
    assert (d.state, d.human_reason, d.decided_by) == ("needs_human", "deploy", "rule")
    d = decide(l0, None, {**POL, "tripwire_mode": "hint"})
    assert (d.state, d.hint) == ("idle_done", "deploy: Ready. Approve the deploy of v2?")
    d = decide(l0, answers("asking_question"), {**POL, "tripwire_mode": "hint"})
    assert d.state == "waiting_reply"


def test_decisive_rules_skip_model():
    busy = evaluate(claude_screen(["● x", "✶ Forging… (5s · ↓ 1k tokens)"]), "claude")
    assert not needs_model(busy)
    assert decide(busy, None, POL).state == "busy"
    assert needs_model(evaluate(IDLE_DONE, "claude"))


def test_rules_only_defaults_to_idle_done():
    d = decide(evaluate(IDLE_DONE, "claude"), None, POL)
    assert (d.state, d.decided_by) == ("idle_done", "rule")


# ---- decide: request shape, key handling, cache


def test_call_shape_and_key_from_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k-test")
    seen = {}

    def fake(url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=body)
        return 200, {"answers": answers(), "usage": {"total_tokens": 9}}

    cfg = dec.load_layer1()
    out = dec.call('{"last_message": "hi"}', cfg, fake)
    assert seen["url"].endswith("/v1beta/decisions")
    assert set(seen["body"]) == {"model", "input", "questions"}
    assert seen["body"]["model"] == "gemini-decision-1"
    assert seen["headers"]["x-goog-api-key"] == "k-test"
    assert "k-test" not in json.dumps(seen["body"])
    assert out["usage"]["total_tokens"] == 9


def test_call_without_key_fails(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        dec.call("x", dec.load_layer1(), lambda *a: (200, {}))


def test_build_input_has_receipts_not_box_text():
    l0 = evaluate(claude_screen(["● Done."], box="❯\xa0\x1b[2mgo deploy\x1b[0m"), "claude")
    payload = json.loads(dec.build_input(l0))
    assert payload["last_message"] == "Done."
    assert payload["facts"]["pane_command"] == "claude"
    assert "go deploy" not in json.dumps(payload)


def test_run_batch_reuses_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = []

    def fake(url, headers, body, timeout):
        calls.append(1)
        return 200, {"answers": answers(), "usage": {}}

    items = {"a": evaluate(IDLE_DONE, "claude")}
    out = tmp_path / "run.jsonl"
    dec.run_batch(items, out, transport=fake)
    dec.run_batch(items, out, transport=fake)
    assert len(calls) == 1
    assert "k" not in out.read_text().replace('"', " ").split()


# ---- split


def rows():
    out = []
    spec = [
        ("idle_done", "none", "live", 30),
        ("busy", "none", "live", 30),
        ("needs_human", "deploy", "live", 10),
        ("needs_human", "spend", "staged", 3),
        ("needs_human", "delete", "staged", 5),
        ("needs_human", "machine", "staged", 3),
        ("dropped_to_shell", "none", "staged", 6),
    ]
    for state, reason, source, n in spec:
        for i in range(n):
            out.append(
                {
                    "id": f"{state}{reason}{i}",
                    "session": "s",
                    "sha256": f"{state}{reason}{i}",
                    "state": state,
                    "human_reason": reason,
                    "source": source,
                }
            )
    # a stalled session: 8 identical captures
    for i in range(8):
        out.append(
            {
                "id": f"stall{i}",
                "session": "x",
                "sha256": "same",
                "state": "idle_done",
                "human_reason": "none",
                "source": "live",
            }
        )
    return out


def test_split_size_min_reason_groups_and_determinism():
    h, t = split(rows(), n=30)
    assert len(h) == 30 and len(h) + len(t) == len(rows())
    c = Counter(r["human_reason"] for r in h)
    assert all(c[r] >= 2 for r in ("deploy", "spend", "delete", "machine"))
    stall_in_h = sum(r["id"].startswith("stall") for r in h)
    assert stall_in_h in (0, 8)
    assert split(rows(), n=30) == (h, t)
    assert {r["id"] for r in h}.isdisjoint({r["id"] for r in t})


def test_dedupe_keeps_earliest_and_rejects_conflicts():
    base = {"session": "m", "sha256": "h", "state": "needs_human", "human_reason": "deploy"}
    rows_ = [
        {**base, "id": "b", "ts": "2"},
        {**base, "id": "a", "ts": "1"},
        {**base, "id": "c", "ts": "1", "sha256": "other"},
    ]
    kept, dropped = dedupe(rows_)
    assert [r["id"] for r in kept] == ["a", "c"] and dropped == 1
    with pytest.raises(ValueError):
        conflict = {**base, "id": "z", "ts": "3", "state": "idle_done", "human_reason": "none"}
        dedupe([*rows_, conflict])


# ---- display tiers (red = needs you, yellow = check); the reported set is unchanged


def test_tiers():
    pol = {**POL, "red_threshold": 0.9}
    plain = evaluate(IDLE_DONE, "claude")
    assert decide(plain, answers(deploy=0.95), pol).tier == "red"
    assert decide(plain, answers(deploy=0.5), pol).tier == "yellow"
    assert decide(plain, answers(conf=0.1), pol).tier == "yellow"  # low confidence
    # tripwire alone is yellow; tripwire next to an approval cue is red
    alone = evaluate(claude_screen(["● Before the deploy, tests passed."]), "claude")
    assert decide(alone, None, pol).tier == "yellow"
    cued = evaluate(
        claude_screen(["● Patch ready to deploy.", "  Waiting for your approval."]), "claude"
    )
    assert decide(cued, None, pol).tier == "red"
    trust = evaluate("Yes, I trust this folder", "claude")
    assert decide(trust, None, pol).tier == "red"


def test_model_evidence_prefers_approval_cue():
    body = [
        "● Status of E1.",
        "  E1 is still waiting for the user's own approval.",
        "  Nothing else is running,",
        "  so I'm standing by.",
    ]
    l0 = evaluate(claude_screen(body), "claude")
    d = decide(l0, answers("awaiting_approval", deploy=0.95), {**POL, "tripwire_mode": "hint"})
    assert d.evidence == "E1 is still waiting for the user's own approval."
