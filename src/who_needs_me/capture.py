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
import json
import re
import shlex
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from who_needs_me.redact import strip_ansi

ALLOWED_SUBCOMMANDS = frozenset({"list-panes", "capture-pane"})
# One ssh connection is kept open and reused: a capture round is several tmux calls, and a new
# connection for each one is what made the board lag. BatchMode: never prompt, fail instead.
SSH_OPTS = [
    *("-o", "BatchMode=yes"),
    *("-o", "ConnectTimeout=8"),
    *("-o", "ControlMaster=auto"),
    *("-o", "ControlPath=~/.ssh/wnm-%C"),
    *("-o", "ControlPersist=120"),
]
Runner = Callable[..., subprocess.CompletedProcess]
# The last five fields are receipts tmux gives for free: the pane size (a resize re-wraps
# everything), whether a full-screen program owns the pane, the note a tool's own hook left on
# the pane (``hook.py``), and the terminal title (several agents write their state into it).
# They go into ``<stem>.meta.json`` next to the capture.
PANE_FORMAT = (
    "#{session_name}\t#{session_name}:#{window_index}.#{pane_index}"
    "\t#{pane_current_command}\t#{window_active}#{pane_active}"
    "\t#{pane_width}\t#{pane_height}\t#{alternate_on}\t#{@wnm}\t#{pane_title}"
)
WIDE = 100  # columns the default --lines was chosen for


class ForbiddenTmuxCommand(RuntimeError):
    pass


def tmux(args: list[str], ssh: str | None = None, runner: Runner = subprocess.run) -> str:
    if not args or args[0] not in ALLOWED_SUBCOMMANDS:
        raise ForbiddenTmuxCommand(f"refusing tmux {args[:1]}: read-only capture only")
    cmd = ["tmux", *args]
    if ssh:
        cmd = ["ssh", *SSH_OPTS, ssh, shlex.join(cmd)]
    return runner(cmd, capture_output=True, text=True, check=True, timeout=20).stdout


def list_sessions(ssh: str | None = None, runner: Runner = subprocess.run) -> list[dict]:
    """Active pane of each session: name, target and current command."""
    out = tmux(["list-panes", "-a", "-F", PANE_FORMAT], ssh, runner)
    panes: dict[str, dict] = {}
    for line in out.splitlines():
        parts = line.split("\t", 8)
        if len(parts) < 4:
            continue
        name, target, cmd, active = parts[:4]
        if active == "11" or name not in panes:
            panes[name] = {"session": name, "target": target, "command": cmd}
            if len(parts) >= 8:
                width, height, alt, note = parts[4:8]
                panes[name]["meta"] = {
                    "width": int(width) if width.isdigit() else None,
                    "height": int(height) if height.isdigit() else None,
                    "alternate": alt == "1",
                    "hook": note.strip(),
                    "title": parts[8].strip() if len(parts) > 8 else "",
                }
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
    seen: dict | None = None,
    keepalive: float = 60,
) -> list[Path]:
    """Capture every session once. With ``seen`` (kept by the caller between rounds), a
    screen that has not changed is written again only every ``keepalive`` seconds, so a
    fast loop does not fill the folder with identical files; the heartbeat file says the
    loop is alive in between."""
    when = now or datetime.now(UTC)
    ts = when.strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    names = []
    for p in list_sessions(ssh, runner):
        if only and p["session"] not in only:
            continue
        names.append(p["session"])
        meta = p.get("meta") or {}
        # a narrow pane holds the same text in more lines
        scale = -(-WIDE // meta["width"]) if meta.get("width") else 1
        args = ["capture-pane", "-p", "-e", "-t", p["target"], "-S", f"-{lines * max(1, scale)}"]
        ansi = tmux(args, ssh, runner)
        if seen is not None:
            old = seen.get(p["session"])
            # the title is left out: several tools animate a spinner in it
            now_is = (p["command"], ansi, meta.get("hook"), meta.get("alternate"))
            if old and old[0] == now_is and (when - old[1]).total_seconds() < keepalive:
                continue
            seen[p["session"]] = (now_is, when)
        stem = f"{ts}_{safe_name(p['session'])}"
        files = [(".ansi", ansi), (".txt", f"{p['command']}\n{strip_ansi(ansi)}")]
        if meta:
            files.insert(0, (".meta.json", json.dumps(meta, ensure_ascii=False)))
        # .txt last: the reader treats a capture as present once .txt exists
        for suffix, text in files:
            tmp = out_dir / f".{stem}{suffix}.tmp"
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(out_dir / f"{stem}{suffix}")
        written.append(out_dir / f"{stem}.txt")
    if seen is not None:
        heartbeat(out_dir, ssh, ts, names)
    return written


def heartbeat(
    out_dir: Path, ssh: str | None, ts: str, sessions: list[str], error: str = ""
) -> None:
    """Tell the board this loop is alive, which sessions exist, and why it failed if it did."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rec = {"ts": ts, "source": ssh or "local", "sessions": sessions, "error": error}
    path = out_dir / f".heartbeat-{safe_name(ssh or 'local')}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec), encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only tmux capture (never sends keys).")
    ap.add_argument("--out", type=Path, default=Path("captures"))
    ap.add_argument("--ssh", help="run tmux on this host over ssh")
    ap.add_argument("--every", type=float, default=0, help="seconds between captures; 0 = once")
    ap.add_argument("--lines", type=int, default=60)
    ap.add_argument("--session", action="append", help="only these sessions (repeatable)")
    ap.add_argument(
        "--keepalive", type=float, default=60, help="rewrite an unchanged screen this often (s)"
    )
    args = ap.parse_args(argv)
    only = set(args.session) if args.session else None
    if not args.every:
        files = capture_once(args.out, args.ssh, args.lines, only)
        print(f"{datetime.now(UTC):%H:%M:%S} captured {len(files)} sessions")
        return 0
    seen: dict = {}
    last_error = None
    while True:
        try:
            files = capture_once(
                args.out, args.ssh, args.lines, only, seen=seen, keepalive=args.keepalive
            )
            if files or last_error:
                print(
                    f"{datetime.now(UTC):%H:%M:%S} wrote {len(files)} changed screens", flush=True
                )
            last_error = None
        except (subprocess.SubprocessError, OSError) as e:
            # keep going: a dropped network, or an ssh check that needs a person, must not
            # end the loop; the board shows the reason from the heartbeat
            detail = getattr(e, "stderr", None) or str(e)
            if isinstance(detail, bytes):
                # a timed-out command hands back what it had read so far as bytes, even when
                # it was started in text mode
                detail = detail.decode("utf-8", "replace")
            detail = detail.strip() or type(e).__name__
            error = (
                "ssh needs a login check"
                if "additional check" in detail
                else detail.splitlines()[-1][:160]
            )
            if error != last_error:
                print(f"{datetime.now(UTC):%H:%M:%S} capture failed: {error}", flush=True)
            last_error = error
            heartbeat(args.out, args.ssh, datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"), [], error)
        time.sleep(args.every)


if __name__ == "__main__":
    raise SystemExit(main())
