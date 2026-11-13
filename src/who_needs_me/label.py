"""Terminal labeling tool: one keypress gives each screen its gold `state` and `human_reason`.

Each start re-scans the raw directory, re-redacts everything into ``data/screens/``,
and shows the screens that still lack a label (redacted, with colors, so you also
see what will be published). Labels are appended to ``data/labels.jsonl``; the last
row for an id wins, and undo appends a tombstone, so nothing is ever overwritten.

Keys
  b busy            i idle_done        w waiting_reply     h dropped_to_shell
  d needs_human:deploy   s :spend   x :delete   m :machine (login/API error, trust prompt, ...)
  Enter  accept the suggestion (shown when an identical earlier screen is labeled)
  u undo last   n skip   f full screen / tail   !  flag a redaction miss   q quit
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from who_needs_me.redact import Screen, default_raw_dir, read_manifest, sync

STATES = ("busy", "idle_done", "waiting_reply", "needs_human", "dropped_to_shell")
REASONS = ("deploy", "spend", "delete", "machine", "none")

KEYMAP: dict[str, tuple[str, str]] = {
    "b": ("busy", "none"),
    "i": ("idle_done", "none"),
    "w": ("waiting_reply", "none"),
    "h": ("dropped_to_shell", "none"),
    "d": ("needs_human", "deploy"),
    "s": ("needs_human", "spend"),
    "x": ("needs_human", "delete"),
    "m": ("needs_human", "machine"),
}

# Targets from the design doc, section 5.1 (plus the "session broke" class from 14.2).
TARGETS = {
    ("idle_done", "none"): 15,
    ("busy", "none"): 12,
    ("waiting_reply", "none"): 12,
    ("needs_human", "deploy"): 5,
    ("needs_human", "spend"): 5,
    ("needs_human", "delete"): 5,
    ("needs_human", "machine"): 5,
    ("dropped_to_shell", "none"): 8,
}


# ---------------------------------------------------------------- label store


def read_labels(path: Path) -> dict[str, dict]:
    """Fold the append-only log into {id: latest label row}; tombstones remove an id."""
    labels: dict[str, dict] = {}
    if not path.exists():
        return labels
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("deleted"):
            labels.pop(row["id"], None)
        else:
            labels[row["id"]] = row
    return labels


def append_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def make_row(
    screen: Screen, sha: str, state: str, reason: str, now: datetime | None = None, **extra
) -> dict:
    assert state in STATES and reason in REASONS
    assert (state == "needs_human") == (reason != "none")
    now = now or datetime.now(UTC)
    return {
        "id": screen.id,
        "session": screen.session,
        "ts": screen.ts,
        "source": screen.source,
        "state": state,
        "human_reason": reason,
        "sha256": sha,
        "labeled_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        **extra,
    }


# ---------------------------------------------------------------- queue logic


def order(screens: Iterable[Screen], only_staged: bool = False) -> list[Screen]:
    """Live screens grouped by session in time order, then staged ones."""
    if only_staged:
        screens = [s for s in screens if s.source == "staged"]
    return sorted(screens, key=lambda s: (s.source == "staged", s.session, s.ts, s.id))


def suggestion(screen: Screen, sha: str, labels: dict[str, dict]) -> dict | None:
    """Label of the latest earlier screen in the same session with identical content.

    Only a suggestion: the same screen can deserve a different label once it has been
    stale for long, so the human still confirms with Enter.
    """
    best = None
    for row in labels.values():
        same = row["sha256"] == sha and row["session"] == screen.session
        if same and row["ts"] <= screen.ts and (best is None or row["ts"] > best["ts"]):
            best = row
    return best


def stats(labels: dict[str, dict]) -> str:
    counts = Counter((r["state"], r["human_reason"]) for r in labels.values())
    by_source = Counter(r["source"] for r in labels.values())
    lines = [f"labeled {len(labels)}  (live {by_source['live']}, staged {by_source['staged']})"]
    for key, target in TARGETS.items():
        have = counts.get(key, 0)
        mark = "ok " if have >= target else "   "
        name = key[0] if key[1] == "none" else f"{key[0]}:{key[1]}"
        lines.append(f"  {mark}{name:<26}{have:>4} / {target}")
    return "\n".join(lines)


# ---------------------------------------------------------------- interactive loop


@dataclass
class Labeler:
    queue: list[Screen]
    labels_path: Path
    flags_path: Path
    manifest: dict[str, str] = field(default_factory=dict)
    pos: int = 0
    full: bool = False
    history: list[str] = field(default_factory=list)  # ids labeled this run, for undo

    def __post_init__(self) -> None:
        self.labels = read_labels(self.labels_path)
        self._sha: dict[str, str] = {}

    def sha(self, s: Screen) -> str:
        if s.id not in self._sha:
            self._sha[s.id] = s.sha256()
        return self._sha[s.id]

    def current(self) -> Screen | None:
        while self.pos < len(self.queue) and self.queue[self.pos].id in self.labels:
            self.pos += 1
        return self.queue[self.pos] if self.pos < len(self.queue) else None

    def handle(self, key: str) -> bool:
        """Apply one keypress. Returns False when the tool should exit."""
        s = self.current()
        if key == "q":
            return False
        if key == "u":
            if self.history:
                last = self.history.pop()
                append_row(self.labels_path, {"id": last, "deleted": True})
                self.labels.pop(last, None)
                self.pos = min(self.pos, next(i for i, q in enumerate(self.queue) if q.id == last))
            return True
        if s is None:
            return False
        if key == "f":
            self.full = not self.full
        elif key == "n":
            # move to the end of the queue
            self.queue.append(self.queue.pop(self.pos))
        elif key == "!":
            append_row(self.flags_path, {"id": s.id, "flag": "redaction_miss"})
        elif key in KEYMAP or key in ("\r", "\n"):
            if key in KEYMAP:
                state, reason = KEYMAP[key]
                extra = {}
            else:
                sug = suggestion(s, self.sha(s), self.labels)
                if sug is None:
                    return True
                state, reason, extra = sug["state"], sug["human_reason"], {"same_as": sug["id"]}
            row = make_row(s, self.sha(s), state, reason, **extra)
            append_row(self.labels_path, row)
            self.labels[s.id] = row
            self.history.append(s.id)
        return True

    def render(self, rows: int) -> str:
        s = self.current()
        if s is None:
            return "all screens labeled.\n\n" + stats(self.labels) + "\n"
        text = s.screen().rstrip("\n")
        lines = text.split("\n")
        if not self.full:
            lines = lines[-max(rows - 9, 5) :]
        remaining = sum(1 for q in self.queue if q.id not in self.labels)
        head = [
            f"\x1b[1m{s.id}\x1b[0m  session={s.session} source={s.source}  "
            f"[{remaining} left, {len(self.labels)} labeled]",
        ]
        # The staging note is the intent, not the truth: show it only after labeling.
        if self.history:
            prev = self.labels.get(self.history[-1])
            if prev:
                note = self.manifest.get(prev["id"])
                said = f"{prev['state']}/{prev['human_reason']}"
                staged = f" · staged as: {note}" if note else ""
                head.append(f"  \x1b[2mprevious {prev['id']}: you said {said}{staged}\x1b[0m")
        sug = suggestion(s, self.sha(s), self.labels)
        if sug:
            head.append(
                f"  \x1b[33msame content as {sug['id']}: {sug['state']}/{sug['human_reason']}"
                "  [Enter accepts]\x1b[0m"
            )
        bar = "\x1b[2m" + "─" * 60 + "\x1b[0m"
        keys = (
            "b busy  i idle_done  w waiting_reply  h shell  | needs_human: d deploy  "
            "s spend  x delete  m machine\nEnter accept  u undo  n skip  f full/tail  "
            "! redaction miss  q quit"
        )
        cmd = s.pane_command()
        if cmd:
            head[0] += f"  pane={cmd}"
        return "\n".join(head + [bar] + lines + ["\x1b[0m" + bar, keys]) + "\n"


def getch() -> str:
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return "q" if ch in ("\x03", "\x04") else ch


def run(labeler: Labeler, read_key: Callable[[], str] = getch) -> None:
    while True:
        rows = shutil.get_terminal_size((100, 40)).lines
        sys.stdout.write("\x1b[2J\x1b[H" + labeler.render(rows))
        sys.stdout.flush()
        if labeler.current() is None or not labeler.handle(read_key()):
            break


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Label redacted screens (see module docstring).")
    ap.add_argument(
        "--raw",
        type=Path,
        default=default_raw_dir(),
        help="directory of raw captures (default: $WNM_RAW_DIR)",
    )
    ap.add_argument("--screens", type=Path, default=Path("data/screens"))
    ap.add_argument("--labels", type=Path, default=Path("data/labels.jsonl"))
    ap.add_argument("--denylist", type=Path, default=Path(".redact.local"))
    ap.add_argument("--stats", action="store_true", help="print label counts and exit")
    ap.add_argument("--only-staged", action="store_true", help="only show staged_ screens")
    args = ap.parse_args(argv)

    if args.stats:
        print(stats(read_labels(args.labels)))
        return 0
    if args.raw is None:
        ap.error("set --raw or WNM_RAW_DIR")

    screens, problems = sync(args.raw, args.screens, args.denylist)
    if problems:
        print("redaction check failed, not labeling until fixed:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    labeler = Labeler(
        order(screens, only_staged=args.only_staged),
        args.labels,
        args.labels.with_name("redaction_flags.jsonl"),
        read_manifest(args.raw),
    )
    run(labeler)
    print(stats(labeler.labels))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
