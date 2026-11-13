"""Pure metric helpers shared by tune.py and eval.py (they read no files)."""

from __future__ import annotations

from collections import Counter

from who_needs_me.policy import Decision, decide, needs_model
from who_needs_me.rules import Layer0

STATES = ("busy", "idle_done", "waiting_reply", "needs_human", "dropped_to_shell")


def predict(
    rows: list[dict], l0s: dict[str, Layer0], answers: dict[str, dict] | None, policy: dict
) -> dict[str, Decision]:
    """answers=None -> rules only. Model answers are only used where layer 1 is needed."""
    out = {}
    for r in rows:
        l0 = l0s[r["id"]]
        a = None
        if answers is not None and needs_model(l0):
            a = answers[r["id"]]["answers"]
        out[r["id"]] = decide(l0, a, policy)
    return out


def score(rows: list[dict], pred: dict[str, Decision]) -> dict:
    gold_nh = [r for r in rows if r["state"] == "needs_human"]
    pred_nh = [r for r in rows if pred[r["id"]].state == "needs_human"]
    tp = [r for r in gold_nh if pred[r["id"]].state == "needs_human"]
    correct = [r for r in rows if pred[r["id"]].state == r["state"]]
    reason_ok = [r for r in tp if pred[r["id"]].human_reason == r["human_reason"]]
    return {
        "n": len(rows),
        "correct": len(correct),
        "nh_gold": len(gold_nh),
        "nh_pred": len(pred_nh),
        "nh_tp": len(tp),
        "misses": len(gold_nh) - len(tp),
        "fp": len(pred_nh) - len(tp),
        "reason_ok": len(reason_ok),
    }


def confusion(rows: list[dict], pred: dict[str, Decision]) -> Counter:
    return Counter((r["state"], pred[r["id"]].state) for r in rows)


def frac(a: int, b: int) -> str:
    return f"{a}/{b}"
