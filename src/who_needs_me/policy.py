"""Layer 2: combine layer-0 rules and layer-1 answers into the output contract.

Labeling conventions this must agree with (decided by the human labeler):
- "waiting on a background task" ends as idle_done; the stale fact stays as a fact.
- any explicit request for the user's stance anywhere in the message -> waiting_reply.
- explicitly waiting for approval to deploy / spend / delete (request or statement)
  -> needs_human with that reason.
- several pending at once -> the most severe: delete > deploy > spend > machine.
- only the last message counts (layer 0 extracts it); input-box text never decides.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path

import yaml

from who_needs_me.rules import CONFIG_PATH, Layer0, approval_line, cue_near, reason_line

REASONS = ("deploy", "spend", "delete", "machine")
TURN_END_STATE = {
    "reporting_done": "idle_done",
    "waiting_on_background": "idle_done",
    "asking_question": "waiting_reply",
    "awaiting_approval": "waiting_reply",  # approval of something not deploy/spend/delete/machine
}


@cache
def load_policy(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["policy"]


@dataclass
class Decision:
    state: str
    human_reason: str
    decided_by: str  # "rule" or "model"
    evidence: str
    probabilities: dict | None
    ghost_text_ignored: bool
    stale_minutes: int | None
    hint: str | None = None  # tripwire hit in hint mode
    tier: str | None = None  # needs_human only: "red" (needs you) or "yellow" (check)

    def to_dict(self) -> dict:
        return asdict(self)


def most_severe(reasons: set[str], severity: list[str]) -> str:
    return min(reasons, key=severity.index)


def needs_model(l0: Layer0) -> bool:
    """Layer 1 is only called when no decisive non-tripwire rule fired."""
    v = _decisive_rule(l0)
    return v is None


def _decisive_rule(l0: Layer0):
    order = l0.config.get("precedence", [])
    hits = [h for h in l0.hits if h.state is not None and h.rule != "tripwire"]
    hits.sort(key=lambda h: order.index(h.rule) if h.rule in order else len(order))
    return hits[0] if hits else None


def decide(l0: Layer0, answers: dict | None, policy: dict | None = None) -> Decision:
    """``answers`` is None for the rules-only scheme."""
    pol = policy or load_policy()
    common = {
        "ghost_text_ignored": l0.facts.get("ghost_text_ignored", False),
        "stale_minutes": l0.facts.get("stale_minutes"),
    }
    rule = _decisive_rule(l0)
    if rule is not None:
        # login/API error, trust prompt, a dialog: red unless the rule itself says "check"
        tier = (rule.tier or "red") if rule.state == "needs_human" else None
        return Decision(rule.state, rule.reason, "rule", rule.evidence, None, tier=tier, **common)

    trip = next((h for h in l0.hits if h.rule == "tripwire"), None)
    hint = None
    reasons: dict[str, str] = {}  # reason -> who flagged it
    if trip is not None:
        if pol["tripwire_mode"] == "escalate":
            reasons[trip.reason] = "rule"
        else:
            hint = f"{trip.reason}: {trip.evidence}"

    probs = None
    if answers is not None:
        probs = {q: a.get("probabilities", a.get("noul")) for q, a in answers.items()}
        for r in REASONS:
            a = answers.get(f"wants_{r}")
            if a is not None and a["noul"] >= pol["wants_threshold"]:
                reasons.setdefault(r, "model")

    if reasons:
        reason = most_severe(set(reasons), pol["severity"])
        by = reasons[reason]
        msg, cfg = l0.last_message, l0.config
        if by == "rule" and trip:
            evidence = trip.evidence
        else:
            evidence = (
                approval_line(msg, cfg) or reason_line(msg, reason, cfg) or l0.evidence_default
            )
        top = max((answers or {}).get(f"wants_{r}", {}).get("noul", 0) for r in REASONS)
        red = top >= pol.get("red_threshold", 1.01) or (
            trip is not None and "rule" in reasons.values() and cue_near(msg, trip.evidence, cfg)
        )
        return Decision(
            "needs_human",
            reason,
            by,
            evidence,
            probs,
            hint=hint,
            tier="red" if red else "yellow",
            **common,
        )

    if answers is None:  # scheme A: a paragraph ending is idle_done
        return Decision("idle_done", "none", "rule", l0.evidence_default, None, hint=hint, **common)

    te = answers["turn_end"]
    if te["choice"] == "unclear" or te["confidence"] < pol["turn_end_min_confidence"]:
        return Decision(
            "needs_human",
            "low_confidence",
            "model",
            l0.evidence_default,
            probs,
            hint=hint,
            tier="yellow",
            **common,
        )
    return Decision(
        TURN_END_STATE[te["choice"]],
        "none",
        "model",
        l0.evidence_default,
        probs,
        hint=hint,
        **common,
    )
