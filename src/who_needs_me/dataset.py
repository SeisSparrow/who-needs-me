"""Load redacted screens + labels and compute layer 0 (with stale minutes) for each."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from who_needs_me.redact import Screen, scan
from who_needs_me.rules import Layer0, content_key, evaluate, stale_minutes

SCREENS = Path("data/screens")
TUNING = Path("data/tuning.jsonl")
HOLDOUT = Path("data/holdout/holdout.jsonl")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")


def layer0_all(screens_dir: Path = SCREENS, config: dict | None = None) -> dict[str, Layer0]:
    """Layer 0 for every screen. Stale minutes come from all captures of the same live
    session up to that point (staged screens have no history, so stale is None)."""
    screens: list[Screen] = sorted(scan(screens_dir, 0), key=lambda s: (s.session, s.ts))
    series: dict[str, list[tuple[str, str]]] = defaultdict(list)
    out = {}
    for s in screens:
        text = s.screen()
        stale = None
        if s.source == "live" and s.ts:
            series[s.session].append((s.ts, content_key(text)))
            stale = stale_minutes(series[s.session])
        out[s.id] = evaluate(text, s.pane_command(), stale, config)
    return out
