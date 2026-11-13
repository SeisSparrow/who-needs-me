"""Read-only screen capture from tmux, in the same file format the classifier reads.

Only two tmux subcommands can ever run: ``list-panes`` and ``capture-pane``. Anything
else (``send-keys`` above all) is refused before a process is started. Nothing is ever
typed into a session.

Output per session and capture: ``<UTC ts>_<session>.ansi`` (with colors) and
``<UTC ts>_<session>.txt`` (first line = pane's current command, then plain text).

    uv run python -m who_needs_me.capture --out captures/ --every 30 [--ssh my-vm]
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from who_needs_me.redact import strip_ansi

ALLOWED_SUBCOMMANDS = frozenset({"list-panes", "capture-pane"})
Runner = Callable[..., subprocess.CompletedProcess]
PANE_FORMAT = (
    "#{session_name}\t#{session_name}:#{window_index}.#{pane_index}"
    "\t#{pane_current_command}\t#{window_active}#{pane_active}"
)


class ForbiddenTmuxCommand(RuntimeError):
    pass


def tmux(args: list[str], ssh: str | None = None, runner: Runner = subprocess.run) -> str:
    if not args or args[0] not in ALLOWED_SUBCOMMANDS:
        raise ForbiddenTmuxCommand(f"refusing tmux {args[:1]}: read-only capture only")
    cmd = ["tmux", *args]
    if ssh:
        cmd = ["ssh", ssh, shlex.join(cmd)]
    return runner(cmd, capture_output=True, text=True, check=True, timeout=20).stdout


def list_sessions(ssh: str | None = None, runner: Runner = subprocess.run) -> list[dict]:
    """Active pane of each session: name, target and current command."""
    out = tmux(["list-panes", "-a", "-F", PANE_FORMAT], ssh, runner)
    panes: dict[str, dict] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        name, target, cmd, active = parts
        if active == "11" or name not in panes:
            panes[name] = {"session": name, "target": target, "command": cmd}
    return list(panes.values())


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]+", "-", name).strip("-") or "session"


def capture_once(
    out_dir: Path,
    ssh: str | None = None,
    lines: int = 60,
    only: set[str] | None = None,
    runner: Runner = subprocess.run,
    now: datetime | None = None,
) -> list[Path]:
    ts = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for p in list_sessions(ssh, runner):
        if only and p["session"] not in only:
            continue
        args = ["capture-pane", "-p", "-e", "-t", p["target"], "-S", f"-{lines}"]
        ansi = tmux(args, ssh, runner)
        stem = f"{ts}_{safe_name(p['session'])}"
        # .ansi first, .txt last: the reader treats a capture as present once .txt exists
        for suffix, text in ((".ansi", ansi), (".txt", f"{p['command']}\n{strip_ansi(ansi)}")):
            tmp = out_dir / f".{stem}{suffix}.tmp"
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(out_dir / f"{stem}{suffix}")
        written.append(out_dir / f"{stem}.txt")
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only tmux capture (never sends keys).")
    ap.add_argument("--out", type=Path, default=Path("captures"))
    ap.add_argument("--ssh", help="run tmux on this host over ssh")
    ap.add_argument("--every", type=float, default=0, help="seconds between captures; 0 = once")
    ap.add_argument("--lines", type=int, default=60)
    ap.add_argument("--session", action="append", help="only these sessions (repeatable)")
    args = ap.parse_args(argv)
    only = set(args.session) if args.session else None
    while True:
        files = capture_once(args.out, args.ssh, args.lines, only)
        print(f"{datetime.now(UTC):%H:%M:%S} captured {len(files)} sessions")
        if not args.every:
            return 0
        time.sleep(args.every)


if __name__ == "__main__":
    raise SystemExit(main())
