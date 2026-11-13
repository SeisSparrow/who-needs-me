"""Choose policy thresholds on the TUNING set only. Never reads the holdout.

Version 2 selection (factory.yaml ``tuning``): tripwire_mode is fixed to escalate by the
design doc (tripwire OR model). Among wants thresholds >= ``wants_floor`` with the fewest
missed needs_human, take the LOWEST one. turn_end_min_confidence is not swept.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from who_needs_me.dataset import HOLDOUT, TUNING, layer0_all, read_jsonl
from who_needs_me.decide import read_run
from who_needs_me.metrics import frac, predict, score
from who_needs_me.policy import load_policy

WANTS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
MODES = ("escalate", "hint")


def load_tuning_rule() -> dict:
    import yaml

    from who_needs_me.rules import CONFIG_PATH

    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["tuning"]


def sweep(rows, l0s, answers, base: dict, floor: float) -> list[tuple[dict, dict]]:
    """All wants thresholds >= floor, escalate mode, ordered by the selection rule."""
    out = []
    for w in WANTS:
        if w < floor:
            continue
        cfg = {**base, "tripwire_mode": "escalate", "wants_threshold": w}
        out.append((cfg, score(rows, predict(rows, l0s, answers, cfg))))
    return sorted(out, key=lambda x: (x[1]["misses"], x[0]["wants_threshold"]))


def row_md(name: str, cfg: dict | None, s: dict) -> str:
    knobs = f"{cfg['wants_threshold']} / {cfg['turn_end_min_confidence']}" if cfg else "—"
    return (
        f"| {name} | {knobs} | {frac(s['nh_tp'], s['nh_gold'])} | "
        f"{frac(s['nh_tp'], s['nh_pred'])} | {s['fp']} | {frac(s['correct'], s['n'])} |"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", type=Path, default=Path("data/runs/tuning.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("data/tuning_report.md"))
    args = ap.parse_args(argv)
    assert args.run.resolve() != HOLDOUT.resolve()
    rows = read_jsonl(TUNING)
    l0s = layer0_all()
    answers = read_run(args.run)
    base = dict(load_policy())

    lines = [
        "# Tuning report (tuning set only)",
        "",
        f"{len(rows)} distinct screens, "
        f"{sum(r['state'] == 'needs_human' for r in rows)} of them needs_human.",
        "",
        "| scheme | wants / min conf | nh recall | nh precision | false nh | 5-class |",
        "|---|---|---|---|---|---|",
    ]
    for mode in MODES:
        cfg = {**base, "tripwire_mode": mode}
        lines.append(
            row_md(f"rules only ({mode})", None, score(rows, predict(rows, l0s, None, cfg)))
        )
    rule = load_tuning_rule()
    assert rule["selection"] == "lowest_wants_among_max_recall"
    results = sweep(rows, l0s, answers, base, rule["wants_floor"])
    lines.append(row_md("rules + Gemini (escalate), chosen", *results[0]))
    lines += [
        "",
        f"## Sweep (rules + Gemini, escalate, min conf {base['turn_end_min_confidence']})",
        "",
        "| mode | wants / min conf | nh recall | nh precision | false nh | 5-class |",
        "|---|---|---|---|---|---|",
    ]
    for cfg, s in sorted(results, key=lambda x: x[0]["wants_threshold"]):
        lines.append(row_md(cfg["tripwire_mode"], cfg, s))
    chosen, s = results[0]
    lines += [
        "",
        f"**chosen** ({rule['selection']}, floor {rule['wants_floor']}): "
        f"tripwire_mode={chosen['tripwire_mode']}, "
        f"wants_threshold={chosen['wants_threshold']}, "
        f"turn_end_min_confidence={chosen['turn_end_min_confidence']}",
    ]
    # red/yellow display tier: highest red precision, ties -> lowest threshold
    lines += [
        "",
        "## Red threshold (display tier only)",
        "",
        "| red_threshold | red | red that truly need a human | yellow |",
        "|---|---|---|---|",
    ]
    tiers = []
    for rt in (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95):
        pred = predict(rows, l0s, answers, {**chosen, "red_threshold": rt})
        red = [r for r in rows if pred[r["id"]].tier == "red"]
        yellow = [r for r in rows if pred[r["id"]].tier == "yellow"]
        good = sum(r["state"] == "needs_human" for r in red)
        tiers.append((-(good / len(red)) if red else 0, rt, len(red), good, len(yellow)))
        lines.append(f"| {rt} | {len(red)} | {good}/{len(red)} | {len(yellow)} |")
    best = min(tiers)
    lines += ["", f"**chosen red_threshold**: {best[1]}"]
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:10]))
    print(lines[-1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
