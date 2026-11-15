"""Print, or merge into a settings file, the hook configuration that makes a tool call ``hook.py``.

    python -m who_needs_me.hooks                 # what each tool supports
    python -m who_needs_me.hooks claude          # the configuration to add, and where
    python -m who_needs_me.hooks claude --write  # merge it into the tool's settings file
                                                 # (the old file is kept as <name>.bak-<time>)
    python -m who_needs_me.hooks codex --script ~/.who-needs-me/hook.py --file ./hooks.json --write

Run it on the machine where the agent runs: hooks are that tool's own configuration, and
``hook.py`` must exist there (it is one file with no dependencies; copy it anywhere).

Nothing here is needed for the board to work. Without hooks it reads the screen, as before.
"""

from __future__ import annotations

import argparse
import copy
import json
import shlex
import sys
import time
from pathlib import Path

HOOK_SCRIPT = Path(__file__).resolve().with_name("hook.py")


def _cmd(script: str, tool: str, event: str, reply: str | None = None) -> str:
    parts = ["python3", script, tool]
    if reply is not None:
        parts += ["--reply", reply]
    return " ".join(shlex.quote(p) if p != script else p for p in [*parts, event])


def _claude_style(tool: str, events: list[str], script: str, reply: str | None = None, **extra):
    """Claude Code's layout, also used by Qwen Code, Codex, Grok Build and Gemini CLI:
    event -> [ {hooks: [ {type: command, command: ...} ]} ]."""
    return {
        "hooks": {
            e: [{"hooks": [{"type": "command", "command": _cmd(script, tool, e, reply), **extra}]}]
            for e in events
        }
    }


def claude(script: str) -> dict:
    events = ["SessionStart", "UserPromptSubmit", "PermissionRequest", "Notification"]
    return _claude_style("claude", [*events, "Stop", "StopFailure", "SessionEnd"], script)


def qwen(script: str) -> dict:
    events = ["SessionStart", "UserPromptSubmit", "PermissionRequest", "Notification"]
    return _claude_style("qwen", [*events, "Stop", "StopFailure", "SessionEnd"], script)


def codex(script: str) -> dict:
    # Codex reads a Stop hook's output as JSON, so the script answers with an empty object
    events = ["SessionStart", "UserPromptSubmit", "PermissionRequest", "Stop", "SessionEnd"]
    return _claude_style("codex", events, script, reply="{}")


def grok(script: str) -> dict:
    events = ["SessionStart", "UserPromptSubmit", "Notification", "Stop", "StopFailure"]
    return _claude_style("grok", [*events, "StopCancelled", "SessionEnd"], script)


def gemini(script: str) -> dict:
    # Gemini CLI wants a name per hook, a timeout in milliseconds, and JSON (or nothing) back
    out: dict = {"hooks": {}}
    for e in ("SessionStart", "BeforeAgent", "Notification", "AfterAgent", "SessionEnd"):
        hook = {
            "name": f"who-needs-me-{e}",
            "type": "command",
            "command": _cmd(script, "gemini", e, "{}"),
            "timeout": 5000,
        }
        out["hooks"][e] = [{"hooks": [hook]}]
    return out


def cursor(script: str) -> dict:
    # beforeSubmitPrompt must answer "continue"; Cursor has no "needs approval" event
    return {
        "version": 1,
        "hooks": {
            "sessionStart": [{"command": _cmd(script, "cursor", "sessionStart")}],
            "beforeSubmitPrompt": [
                {"command": _cmd(script, "cursor", "beforeSubmitPrompt", '{"continue": true}')}
            ],
            "stop": [{"command": _cmd(script, "cursor", "stop")}],
            "sessionEnd": [{"command": _cmd(script, "cursor", "sessionEnd")}],
        },
    }


def kiro(script: str) -> dict:
    # Kiro adds a hook's output to the agent's context, so the script prints nothing.
    # It has no "needs approval" trigger.
    return {
        "version": "v1",
        "hooks": [
            {
                "name": f"who-needs-me-{t}",
                "trigger": t,
                "action": {"type": "command", "command": _cmd(script, "kiro", t)},
                "timeout": 5,
            }
            for t in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd")
        ],
    }


OPENCODE_PLUGIN = """\
// who-needs-me: leaves a note on the tmux pane when opencode needs you or finishes a turn.
const SCRIPT = %(script)s
const EVENTS = [
  "permission.asked", "permission.replied", "session.status", "session.idle",
  "session.created", "session.deleted",
]
export const WhoNeedsMe = async ({ $ }) => {
  const tell = async (event, payload) => {
    const data = JSON.stringify(payload ?? {})
    try {
      await $`python3 ${SCRIPT} opencode ${event} ${data}`.quiet().nothrow()
    } catch {}
  }
  return {
    event: async ({ event }) => {
      if (EVENTS.includes(event.type)) await tell(event.type, event.properties)
    },
    "chat.message": async () => {
      await tell("chat.message", {})
    },
  }
}
"""


def opencode(script: str) -> str:
    return OPENCODE_PLUGIN % {"script": json.dumps(script)}


# tool -> (builder, settings file, how far this was checked, notes)
TOOLS: dict[str, tuple] = {
    "claude": (
        claude,
        "~/.claude/settings.json",
        "run here on Claude Code 2.1.325: every event arrived, the approval note within 5 s",
        [
            "PermissionRequest fires the moment the dialog opens; Notification repeats it "
            "after about 6 s and reports an idle session after about 60 s.",
            "Stop does not fire when you interrupt a turn with Esc.",
        ],
    ),
    "codex": (
        codex,
        "~/.codex/hooks.json",
        "run here on Codex 0.169.0 up to the model call (SessionStart, UserPromptSubmit and "
        "SessionEnd arrived; PermissionRequest and Stop were not reached: no quota)",
        [
            "Codex runs a new hook only after you review it: open /hooks in Codex and trust it.",
            "The older `notify` setting only reports a finished turn; hooks replace it.",
        ],
    ),
    "gemini": (
        gemini,
        "~/.gemini/settings.json",
        "run here on Gemini CLI 0.68.0: working, approval, done and session end all arrived",
        ["Notification fires when a tool needs permission (notification_type ToolPermission)."],
    ),
    "qwen": (
        qwen,
        "~/.qwen/settings.json",
        "from the Qwen Code 0.27 documentation and source; not run here",
        [],
    ),
    "grok": (
        grok,
        "~/.grok/hooks/who-needs-me.json",
        "from the Grok Build documentation; not run here",
        [
            "Grok Build also runs the hooks in ~/.claude/settings.json and ~/.cursor/hooks.json. "
            "hook.py notices (GROK_HOOK_EVENT) and labels the note 'grok'; if Claude Code's "
            "hooks are already installed on this machine you do not need this file.",
        ],
    ),
    "cursor": (
        cursor,
        "~/.cursor/hooks.json",
        "from the Cursor CLI documentation; not run here",
        [
            "Cursor has no event for 'waiting for approval': with hooks the board learns "
            "'working' and 'done'; the approval dialog is still read from the screen.",
            "Cursor also runs Claude Code's hooks from ~/.claude/settings.json.",
        ],
    ),
    "kiro": (
        kiro,
        "~/.kiro/hooks/who-needs-me.json",
        "from the Kiro CLI V3 documentation; not run here",
        ["Kiro has no trigger for 'waiting for approval'; that is read from the screen."],
    ),
    "opencode": (
        opencode,
        "~/.config/opencode/plugins/who-needs-me.js",
        "from the opencode 1.19 plugin documentation and source; not run here",
        ["opencode 2.x uses a different plugin API; this file is for 1.x."],
    ),
}
NO_HOOKS = {
    "trae": "Trae Agent has no hooks, plugins or notifications; it is read from the screen."
}


def merge(base, add):
    """``add`` merged into ``base``: dicts recursively, lists by appending what is missing.
    Running it twice changes nothing."""
    if isinstance(base, dict) and isinstance(add, dict):
        out = dict(base)
        for k, v in add.items():
            out[k] = merge(out[k], v) if k in out else copy.deepcopy(v)
        return out
    if isinstance(base, list) and isinstance(add, list):
        return base + [x for x in add if x not in base]
    return base  # an existing plain value is the user's: keep it


def render(tool: str, script: str) -> tuple[str, str]:
    """(settings path, text to put there)."""
    build, path, _, _ = TOOLS[tool]
    made = build(script)
    return path, made if isinstance(made, str) else json.dumps(made, indent=2) + "\n"


def write(tool: str, script: str, file: Path, now: float | None = None) -> str:
    """Merge the configuration into ``file`` (JSON) or create it. Returns what was done."""
    build = TOOLS[tool][0]
    made = build(script)
    file.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(made, str):
        text = made
    else:
        old = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}
        text = json.dumps(merge(old, made), indent=2) + "\n"
    if file.exists():
        if file.read_text(encoding="utf-8") == text:
            return f"{file}: already set up, nothing changed"
        stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime(now))
        backup = file.with_name(f"{file.name}.bak-{stamp}")
        backup.write_text(file.read_text(encoding="utf-8"), encoding="utf-8")
        file.write_text(text, encoding="utf-8")
        return f"{file}: updated (previous version kept as {backup.name})"
    file.write_text(text, encoding="utf-8")
    return f"{file}: created"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Hook configuration for who-needs-me")
    ap.add_argument("tool", nargs="?", help=" / ".join([*TOOLS, *NO_HOOKS]))
    ap.add_argument("--script", default=str(HOOK_SCRIPT), help="where hook.py is on this machine")
    ap.add_argument("--file", type=Path, help="settings file to use instead of the default")
    ap.add_argument("--write", action="store_true", help="merge into the file (keeps a backup)")
    args = ap.parse_args(argv)
    if not args.tool:
        for name, (_, path, checked, _) in TOOLS.items():
            print(f"{name:9s} {path:44s} {checked}")
        for name, why in NO_HOOKS.items():
            print(f"{name:9s} {why}")
        return 0
    if args.tool in NO_HOOKS:
        print(NO_HOOKS[args.tool])
        return 0
    if args.tool not in TOOLS:
        print(f"unknown tool {args.tool!r}; one of: {', '.join(TOOLS)}", file=sys.stderr)
        return 2
    _, path, checked, notes = TOOLS[args.tool]
    target = args.file or Path(path).expanduser()
    if args.write:
        print(write(args.tool, args.script, target))
    else:
        print(f"# {args.tool}: add this to {target}   (or run again with --write)")
        print(f"# checked: {checked}")
        print(render(args.tool, args.script)[1], end="")
    for n in notes:
        print(f"# note: {n}", file=sys.stderr if not args.write else sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
