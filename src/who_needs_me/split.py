"""Lock the holdout: stratified, group-aware, fixed seed.

- Strata: (state, human_reason, source).
- Screens with identical content in the same session (a stalled session captured many
  times) are deduplicated first: only the earliest capture is kept, so one stalled screen
  cannot count as twelve samples. Groups stay in the split code as a safety net.
- Every needs_human reason gets at least ``MIN_PER_REASON`` holdout screens.

Writes ``data/holdout/holdout.jsonl`` and ``data/tuning.jsonl`` and prints the holdout
sha256 for the README. Tuning code must only read ``data/tuning.jsonl``.
"""

from __future__ import annotations

import argparse
import hashlib
import random
from collections import Counter, defaultdict
from pathlib import Path

from who_needs_me.dataset import HOLDOUT, TUNING, write_jsonl
from who_needs_me.label import read_labels

SEED = 20261113
N_HOLDOUT = 45
MIN_PER_REASON = 2
REASONS = ("deploy", "spend", "delete", "machine")


def stratum(row: dict) -> tuple[str, str, str]:
    return row["state"], row["human_reason"], row["source"]


def dedupe(rows: list[dict]) -> tuple[list[dict], int]:
    """Keep the earliest capture of each (session, content) group. Refuses conflicting labels."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["session"], r["sha256"])].append(r)
    kept = []
    for g in groups.values():
        labels = {(r["state"], r["human_reason"]) for r in g}
        if len(labels) > 1:
            raise ValueError(f"identical screens with different labels: {[r['id'] for r in g]}")
        kept.append(min(g, key=lambda r: (r["ts"], r["id"])))
    return sorted(kept, key=lambda r: r["id"]), len(rows) - len(kept)


def split(
    rows: list[dict], n: int = N_HOLDOUT, seed: int = SEED, min_per_reason: int = MIN_PER_REASON
) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in sorted(rows, key=lambda r: r["id"]):
        groups[(r["session"], r["sha256"])].append(r)
    by_stratum: dict[tuple, list[list[dict]]] = defaultdict(list)
    for g in groups.values():
        by_stratum[stratum(g[0])].append(g)
    for s in sorted(by_stratum):
        rng.shuffle(by_stratum[s])

    frac = n / len(rows)
    picked: list[list[dict]] = []
    rest: list[list[dict]] = []
    for s in sorted(by_stratum):
        target = round(frac * sum(len(g) for g in by_stratum[s]))
        got = 0
        for g in by_stratum[s]:
            if got + len(g) <= target:
                picked.append(g)
                got += len(g)
            else:
                rest.append(g)

    def count(gs, pred):
        return sum(len(g) for g in gs if pred(g[0]))

    # each needs_human reason at least min_per_reason (smallest groups first)
    for reason in REASONS:

        def is_r(r, reason=reason):
            return r["state"] == "needs_human" and r["human_reason"] == reason

        cands = sorted((g for g in rest if is_r(g[0])), key=len)
        while count(picked, is_r) < min_per_reason and cands:
            g = cands.pop(0)
            rest.remove(g)
            picked.append(g)

    # hit exactly n with single screens: add from / drop to the most under/over strata
    def size(gs):
        return sum(len(g) for g in gs)

    def gap(s):  # wanted minus have, for stratum s
        total = sum(len(g) for g in by_stratum[s])
        return frac * total - count(picked, lambda r: stratum(r) == s)

    while size(picked) < n:
        singles = [g for g in rest if len(g) == 1]
        g = max(singles, key=lambda g: (gap(stratum(g[0])), g[0]["id"]))
        rest.remove(g)
        picked.append(g)
    while size(picked) > n:

        def removable(g):
            r = g[0]
            if len(g) != 1:
                return False
            if r["state"] == "needs_human" and r["human_reason"] in REASONS:
                return (
                    count(picked, lambda x: x["human_reason"] == r["human_reason"]) > min_per_reason
                )
            return True

        g = min((g for g in picked if removable(g)), key=lambda g: (gap(stratum(g[0])), g[0]["id"]))
        picked.remove(g)
        rest.append(g)

    holdout = sorted((r for g in picked for r in g), key=lambda r: r["id"])
    tuning = sorted((r for g in rest for r in g), key=lambda r: r["id"])
    return holdout, tuning


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lock the holdout split.")
    ap.add_argument("--labels", type=Path, default=Path("data/labels.jsonl"))
    args = ap.parse_args(argv)
    if HOLDOUT.exists():
        print(f"{HOLDOUT} already exists; refusing to re-split. sha256={sha256_file(HOLDOUT)}")
        return 1
    rows, dropped = dedupe(list(read_labels(args.labels).values()))
    print(f"labels {len(rows) + dropped}, duplicates dropped {dropped}, distinct {len(rows)}")
    holdout, tuning = split(rows)
    write_jsonl(HOLDOUT, holdout)
    write_jsonl(TUNING, tuning)
    print(f"holdout {len(holdout)}  tuning {len(tuning)}  seed {SEED}")
    for k, v in sorted(Counter(stratum(r) for r in holdout).items()):
        print(f"  {'/'.join(k):<36}{v}")
    print(f"sha256({HOLDOUT}) = {sha256_file(HOLDOUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
