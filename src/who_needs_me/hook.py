#!/usr/bin/env python3
"""Tell who-needs-me what an agent is doing, from the agent's own hook system.

Reading the screen is a guess; a hook is a receipt. An agent tool calls this script from its
hooks ("the user sent a prompt", "I need permission", "my turn is over"), and the script
leaves a one-line note on the tmux pane the agent runs in:

    tmux set-option -p -t "$TMUX_PANE" @wnm "<unix time> <tool> <kind> <event>"

The capture loop already asks tmux for every pane (``list-panes``), so the note travels with
that call. Nothing else is written, nothing is sent anywhere, and the capture stays read-only.

This file is standalone on purpose (standard library only): copy it to any machine and point
the tool's hooks at it. See ``python -m who_needs_me.hooks`` for each tool's configuration.

    python3 hook.py <tool> <event>            # payload, if any, as JSON on stdin
    python3 hook.py <tool> <event> '<json>'   # ...or as one argument (Codex "notify")
    python3 hook.py <tool> --reply '<json>' <event>   # print this on stdout (hooks that
                                                      # must answer, e.g. Cursor)

Kinds: ``working`` (busy), ``approval`` (blocked until a person allows something),
``done`` (turn finished), ``waiting`` (idle, waiting for input), ``end`` (session closed).

It always exits 0 and, unless ``--reply`` is given, prints nothing: several tools read a
hook's output or exit code, and a monitor must never change what the agent does.
"""

from __future__ import annotations

import json
import os
import re
import select
import subprocess
import sys
import time

OPTION = "@wnm"
KINDS = ("working", "approval", "done", "waiting", "end")

# event name (lower case, letters only) -> kind. Names differ by tool; meanings do not.
EVENTS = {
    # a person, or the agent itself, started work
    "userpromptsubmit": "working",  # Claude Code, Kiro, Codex, Qwen
    "beforesubmitprompt": "working",  # Cursor
    "beforeagent": "working",  # Gemini CLI, Qwen Code
    "pretooluse": "working",
    "posttooluse": "working",
    "beforetool": "working",
    "aftertool": "working",
    "beforeshellexecution": "working",
    "aftershellexecution": "working",
    "afterfileedit": "working",
    "chatmessage": "working",  # opencode
    "sessionstart": "waiting",
    "agentspawn": "waiting",  # Kiro 2.x
    "sessioncreated": "waiting",  # opencode
    # blocked on a person
    "permissionrequest": "approval",
    "permissionasked": "approval",  # opencode
    "permissionreplied": "working",  # opencode: the person answered
    "approvalrequested": "approval",
    # turn over
    "stop": "done",
    "afteragent": "done",
    "agentturncomplete": "done",  # Codex notify
    "sessionidle": "done",  # opencode
    "stopfailure": "done",  # ended on an API error: the screen shows which
    "stopcancelled": "done",  # Grok Build: interrupted
    "interrupt": "done",  # Codex: interrupted
    "turncomplete": "done",  # Grok Build notification hooks ($GROK_EVENT)
    "approvalrequired": "approval",
    "sessionready": "waiting",
    # closed
    "sessionend": "end",
    "sessiondeleted": "end",
}


def norm(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def kind_of(event: str, payload: dict) -> str | None:
    """What the event means. ``None`` = nothing worth noting (the note is left as it is)."""
    if event in KINDS:
        return event
    name = norm(str(event_name(payload) or event))
    if name == "notification":
        # one event, several meanings: the type (or, on older versions, the text) says which
        kind = norm(str(payload.get("notification_type") or payload.get("type") or ""))
        text = str(payload.get("message") or "").lower()
        if "permission" in kind or "elicitation" in kind or "permission" in text:
            return "approval"
        if "idle" in kind or "waiting for your input" in text:
            return "waiting"
        return None
    if name == "sessionstatus":  # opencode: {"status": {"type": "busy" | "idle" | "retry"}}
        status = payload.get("status")
        state = status.get("type") if isinstance(status, dict) else status
        return {"busy": "working", "retry": "working", "idle": "done"}.get(str(state))
    return EVENTS.get(name) or EVENTS.get(norm(event))


def event_name(payload: dict) -> str:
    """The event as the tool names it (Grok Build sends camelCase keys)."""
    for key in ("hook_event_name", "hookEventName", "type"):
        if isinstance(payload.get(key), str) and payload[key]:
            return payload[key]
    return ""


def real_tool(tool: str, env) -> str:
    """Grok Build and Cursor also run the hooks in Claude Code's settings file, so the name
    given on the command line can be wrong; their own environment variables say who called."""
    if env.get("GROK_HOOK_EVENT") or env.get("GROK_EVENT"):
        return "grok"
    if env.get("CURSOR_VERSION") or env.get("CURSOR_AGENT"):
        return "cursor"
    return tool


def read_payload(arg: str | None) -> dict:
    text = arg
    if text is None and not sys.stdin.isatty():
        # a hook's stdin is a pipe that closes; never wait on anything else
        ready, _, _ = select.select([sys.stdin], [], [], 0.5)
        text = sys.stdin.read() if ready else ""
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def note(tool: str, kind: str, event: str, now: float | None = None) -> str:
    """The pane note: four space-separated fields, so tmux prints it on one line."""
    clean = re.sub(r"[^A-Za-z0-9_.-]", "", event)[:40] or "-"
    return f"{int(now if now is not None else time.time())} {tool} {kind} {clean}"


def set_note(pane: str, value: str | None, run=subprocess.run) -> None:
    cmd = ["tmux", "set-option", "-p", "-t", pane]
    cmd += ["-u", OPTION] if value is None else [OPTION, value]
    run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, check=False)


def main(argv: list[str], env=os.environ, run=subprocess.run) -> int:
    args = list(argv)
    reply = None
    if "--reply" in args:
        i = args.index("--reply")
        reply = args[i + 1] if i + 1 < len(args) else None
        del args[i : i + 2]
    try:
        if len(args) < 2:
            return 0
        tool = real_tool(re.sub(r"[^a-z0-9_-]", "", args[0].lower())[:20] or "agent", env)
        event = env.get("GROK_EVENT") or args[1]
        payload = read_payload(args[2] if len(args) > 2 else None)
        pane = env.get("TMUX_PANE")
        kind = kind_of(event, payload)
        if pane and kind:
            name = event_name(payload) or event
            set_note(pane, None if kind == "end" else note(tool, kind, name), run)
    except Exception:  # noqa: BLE001 - a monitor must never break the agent it watches
        pass
    finally:
        if reply is not None:
            print(reply)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
