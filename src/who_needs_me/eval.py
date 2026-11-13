"""Evaluation on the locked holdout. Every number is reported as "k/n".

eval.md has two parts:

1. First evaluation, with the policy fixed in advance (``policy_v1``). Its text is frozen
   in ``data/eval_v1.md`` and copied verbatim; this script only re-derives its numbers
   (layer 0 without the later empty_session rule, cached runs ``holdout_{1,2}``) and
   refuses to continue if they no longer match.
2. Second evaluation, after having seen the first one (``policy`` + current layer 0,
   fresh runs ``holdout_v2_{1,2}``). For reference only.

The model is called twice per screen in each part (repeat consistency); reported metrics
use the first run. Cached runs are reused, so regenerating eval.md makes no new calls.
"""

from __future__ import annotations

import argparse
import statistics
import time
from pathlib import Path

import yaml

from who_needs_me.dataset import HOLDOUT, layer0_all, read_jsonl
from who_needs_me.decide import read_run, run_batch
from who_needs_me.metrics import STATES, confusion, frac, predict, score
from who_needs_me.policy import load_policy, needs_model
from who_needs_me.rules import CONFIG_PATH, load_config
from who_needs_me.split import sha256_file

RUNS = Path("data/runs")
FROZEN_V1 = Path("data/eval_v1.md")
MODEL = "rules + Gemini Decision 1"


def p95(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[max(0, int(round(0.95 * len(xs))) - 1)]


def nh_cells(rows, pred, source=None) -> tuple[str, str]:
    rs = [r for r in rows if source is None or r["source"] == source]
    s = score(rs, pred)
    return frac(s["nh_tp"], s["nh_gold"]), frac(s["nh_tp"], s["nh_pred"])


def scheme_row(name: str, rows, pred) -> str:
    s = score(rows, pred)
    rl, pl = nh_cells(rows, pred, "live")
    rs, ps = nh_cells(rows, pred, "staged")
    return (
        f"| {name} | {frac(s['nh_tp'], s['nh_gold'])} | {frac(s['nh_tp'], s['nh_pred'])} "
        f"| {rl} | {pl} | {rs} | {ps} | {frac(s['correct'], s['n'])} "
        f"| {frac(s['reason_ok'], s['nh_tp'])} |"
    )


SCHEME_HEAD = [
    "| scheme | nh recall | nh precision | recall live | precision live | recall staged "
    "| precision staged | 5-class correct | reason correct (of caught) |",
    "|---|---|---|---|---|---|---|---|---|",
]


def evaluate_version(
    rows, l0_config: dict | None, pol: dict, run_names: tuple[str, str], call_model: bool
) -> dict:
    t0 = time.perf_counter()
    l0s = layer0_all(config=l0_config)
    rules_ms = (time.perf_counter() - t0) / max(len(l0s), 1) * 1000
    items = {r["id"]: l0s[r["id"]] for r in rows if needs_model(l0s[r["id"]])}
    paths = [RUNS / f"{n}.jsonl" for n in run_names]
    if call_model:
        for p in paths:
            run_batch(items, p)
    run1, run2 = (read_run(p) for p in paths)
    missing = [i for i in items if i not in run1 or i not in run2]
    if missing:
        raise RuntimeError(f"no cached model answers for {missing[:3]}…")
    schemes = {
        "rules only (tripwire escalate)": predict(
            rows, l0s, None, {**pol, "tripwire_mode": "escalate"}
        ),
        "rules only (tripwire hint)": predict(rows, l0s, None, {**pol, "tripwire_mode": "hint"}),
        MODEL: predict(rows, l0s, run1, pol),
    }
    pred2 = predict(rows, l0s, run2, pol)
    p1 = schemes[MODEL]
    same = sum(
        (p1[r["id"]].state, p1[r["id"]].human_reason)
        == (pred2[r["id"]].state, pred2[r["id"]].human_reason)
        for r in rows
    )
    return {
        "l0s": l0s,
        "items": items,
        "run1": run1,
        "run2": run2,
        "schemes": schemes,
        "same": same,
        "rules_ms": rules_ms,
        "pol": pol,
    }


def render(rows, v: dict, header: list[str]) -> list[str]:
    items, run1, run2, pol = v["items"], v["run1"], v["run2"], v["pol"]
    model_pred = v["schemes"][MODEL]
    s_model = score(rows, model_pred)
    lat = [run1[i]["latency_s"] for i in items] + [run2[i]["latency_s"] for i in items]
    tok1 = sum(run1[i]["usage"].get("total_tokens", 0) for i in items)
    tok2 = sum(run2[i]["usage"].get("total_tokens", 0) for i in items)
    n_live = sum(r["source"] == "live" for r in rows)
    L = [
        *header,
        f"- Holdout: `data/holdout/holdout.jsonl`, {len(rows)} distinct screens "
        f"({n_live} live, {len(rows) - n_live} staged), "
        f"{sum(r['state'] == 'needs_human' for r in rows)} labeled needs_human.",
        f"- sha256: `{sha256_file(HOLDOUT)}`",
        f"- Policy: tripwire_mode={pol['tripwire_mode']}, "
        f"wants_threshold={pol['wants_threshold']}, "
        f"turn_end_min_confidence={pol['turn_end_min_confidence']}.",
        f"- Layer 1 was needed for {len(items)}/{len(rows)} screens; the rest were decided "
        "by layer-0 rules without a model call.",
        "",
        "## Headline",
        "",
        f"- needs_human caught: **{frac(s_model['nh_tp'], s_model['nh_gold'])}** (rules + Gemini).",
        f"- Same final decision (state and reason) on two runs: **{frac(v['same'], len(rows))}**.",
        f"- Escalation rate: **{frac(s_model['nh_pred'], len(rows))}** screens flagged "
        "needs_human.",
        "",
        "## Three schemes",
        "",
        *SCHEME_HEAD,
        *(scheme_row(name, rows, pred) for name, pred in v["schemes"].items()),
        "",
        "Precision `0/0` means the scheme flagged nothing in that slice.",
        "",
        "## Confusion matrix (rules + Gemini, run 1)",
        "",
        "rows = label, columns = prediction",
        "",
        "| label \\ pred | " + " | ".join(STATES) + " |",
        "|---|" + "---|" * len(STATES),
    ]
    cm = confusion(rows, model_pred)
    for g in STATES:
        L.append(f"| {g} | " + " | ".join(str(cm.get((g, p), 0)) for p in STATES) + " |")
    L += [
        "",
        "## Cost and latency",
        "",
        f"- Model calls: {len(items)} per run, 2 runs; latency per call median "
        f"{statistics.median(lat):.2f} s, p95 {p95(lat):.2f} s (n={len(lat)}).",
        f"- Layer 0 (rules) per screen: {v['rules_ms']:.1f} ms on average.",
        f"- Tokens: run 1 {tok1}, run 2 {tok2} (input only; the API reports 0 output tokens).",
        "- Money: the Decisions API response has no cost field, so no dollar figure is "
        "reported here.",
        "",
        "## Every wrong prediction (rules + Gemini, run 1)",
        "",
    ]
    wrong = [
        r
        for r in rows
        if (model_pred[r["id"]].state, model_pred[r["id"]].human_reason)
        != (r["state"], r["human_reason"])
    ]
    L.append(f"{len(wrong)} of {len(rows)} differ from the label in state or reason.")
    for r in wrong:
        d = model_pred[r["id"]]
        msg = v["l0s"][r["id"]].last_message.strip() or "(empty)"
        L += [
            "",
            f"### `{r['id']}` ({r['source']})",
            "",
            f"- label: {r['state']}/{r['human_reason']} · prediction: {d.state}/{d.human_reason}"
            f" (decided by {d.decided_by})" + (f" · tripwire hint: {d.hint}" if d.hint else ""),
            "",
            "```text",
            msg,
            "```",
        ]
    return L


def check_frozen_v1(rows, v1: dict, frozen: str) -> None:
    """The frozen first evaluation must still be what the v1 setup produces."""
    s = score(rows, v1["schemes"][MODEL])
    must = [
        f"needs_human caught: **{frac(s['nh_tp'], s['nh_gold'])}**",
        f"on two runs: **{frac(v1['same'], len(rows))}**",
        *(scheme_row(n, rows, p) for n, p in v1["schemes"].items()),
    ]
    for m in must:
        if m not in frozen:
            raise RuntimeError(f"frozen first evaluation no longer reproduces: {m}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("eval.md"))
    args = ap.parse_args(argv)
    rows = read_jsonl(HOLDOUT)
    cfg_all = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

    l0_v1 = dict(load_config())
    l0_v1["rules"] = {**l0_v1["rules"], "empty_session": False}
    v1 = evaluate_version(
        rows, l0_v1, cfg_all["policy_v1"], ("holdout_1", "holdout_2"), call_model=False
    )
    frozen = FROZEN_V1.read_text(encoding="utf-8")
    check_frozen_v1(rows, v1, frozen)

    v2 = evaluate_version(
        rows, None, dict(load_policy()), ("holdout_v2_1", "holdout_v2_2"), call_model=True
    )
    part2 = render(
        rows,
        v2,
        [
            "# 第二次评估（看过留出集之后）",
            "",
            "这组数是在看过留出集结果后得到的，只能当参考。",
            "",
            "Three changes, each decided from the design doc and the tuning set only (no holdout "
            "probabilities were used to pick a threshold):",
            "",
            "1. Tripwire and model are OR-ed again (design doc 4.4): a tripwire hit, or any "
            "wants_* over the threshold, gives needs_human.",
            "2. Threshold selection: among settings tied on recall, take the lowest wants "
            "threshold, floor 0.2 (design value); on the tuning set this gives 0.2.",
            "3. New layer-0 rule `empty_session`: a freshly started session with no "
            "conversation is idle_done and is not sent to the model.",
            "",
        ],
    )

    pv2 = v2["schemes"][MODEL]
    flagged = [r for r in rows if pv2[r["id"]].state == "needs_human"]
    red = [r for r in flagged if pv2[r["id"]].tier == "red"]
    yellow = [r for r in flagged if pv2[r["id"]].tier != "red"]
    red_ok = sum(r["state"] == "needs_human" for r in red)
    yellow_ok = sum(r["state"] == "needs_human" for r in yellow)
    part2 += [
        "",
        "## Red / yellow split of what was reported",
        "",
        "这也是看过留出集之后的数。The dashboard splits reported needs_human into red "
        f"(needs you) and yellow (to check), red_threshold={v2['pol'].get('red_threshold')} "
        "chosen on the tuning set. The reported set and its recall are unchanged.",
        "",
        f"- Of the {len(flagged)} reported: red {len(red)}, yellow {len(yellow)}.",
        f"- Red that truly need a human: {frac(red_ok, len(red))}.",
        f"- Yellow that truly need a human: {frac(yellow_ok, len(yellow))}.",
    ]

    s1, s2 = score(rows, v1["schemes"][MODEL]), score(rows, v2["schemes"][MODEL])
    compare = [
        "# 两次评估并排",
        "",
        "| version " + SCHEME_HEAD[0],
        "|---" + SCHEME_HEAD[1],
    ]
    for tag, v in (("1 (fixed in advance)", v1), ("2 (after seeing holdout)", v2)):
        for name, pred in v["schemes"].items():
            compare.append(f"| {tag} " + scheme_row(name, rows, pred))
    extra_fp = s2["fp"] - s1["fp"]
    compare += [
        "",
        f"**Cost of version 2:** rules + Gemini went from {s1['fp']} to {s2['fp']} false "
        f"needs_human ({extra_fp:+d}), escalation rate {frac(s1['nh_pred'], len(rows))} → "
        f"{frac(s2['nh_pred'], len(rows))}, for needs_human recall "
        f"{frac(s1['nh_tp'], s1['nh_gold'])} → {frac(s2['nh_tp'], s2['nh_gold'])}.",
        "",
    ]
    text = "\n".join([*compare, frozen.rstrip("\n"), "", *part2]) + "\n"
    args.out.write_text(text, encoding="utf-8")
    print("\n".join(compare))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
