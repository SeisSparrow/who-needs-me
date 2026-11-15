"""Layer 0 for terminal agents other than Claude Code, driven by ``profiles.yaml``.

A profile is a list of things one tool prints: its busy line, its "may I?" dialog, its login
screen, how a submitted prompt and an answer look. The rules here are the same for every
tool; only the strings differ. The outcome is the same ``Layer0`` the Claude Code rules
produce, so the decision model and the policy do not know which tool they are looking at.

``detect`` decides which tool owns a pane. tmux's process name is the receipt when it names
the tool (``codex``, ``claude``); when it is only ``node`` or ``python``, the lines the tool
leaves on screen decide, the most recent one winning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import yaml

PROFILES_PATH = Path(__file__).resolve().parents[2] / "profiles.yaml"
# what tmux reports for a tool that runs inside an interpreter
RUNTIME_RE = re.compile(r"^(?:node|bun|deno|python[\d.]*|uv|ruby|java|npm|npx)$", re.I)
BOX_RE = re.compile(r"[─-╿]+")
DIALOG_TAIL = 30
BUSY_TAIL = 8
ERROR_TAIL = 6


def _compile(value) -> list[re.Pattern[str]]:
    if not value:
        return []
    return [re.compile(v) for v in ([value] if isinstance(value, str) else value)]


@dataclass
class Profile:
    name: str
    label: str
    verified: str
    commands: frozenset[str]
    command_patterns: list[re.Pattern[str]]
    markers: list[re.Pattern[str]]
    title: dict[str, re.Pattern[str]] = field(default_factory=dict)
    chrome: list[re.Pattern[str]] = field(default_factory=list)
    busy: list[re.Pattern[str]] = field(default_factory=list)
    approval: list[re.Pattern[str]] = field(default_factory=list)
    auth: list[re.Pattern[str]] = field(default_factory=list)
    trust: list[re.Pattern[str]] = field(default_factory=list)
    error: list[re.Pattern[str]] = field(default_factory=list)
    user: list[re.Pattern[str]] = field(default_factory=list)
    assistant: list[re.Pattern[str]] = field(default_factory=list)
    done: list[re.Pattern[str]] = field(default_factory=list)
    noise: list[re.Pattern[str]] = field(default_factory=list)
    continuation: list[re.Pattern[str]] = field(default_factory=list)  # wrapped prompt lines
    done_required: bool = False  # a turn always ends with the ``done`` line
    dialog_tail: int = DIALOG_TAIL  # a dialog is looked for in this many last lines

    @classmethod
    def from_dict(cls, name: str, d: dict) -> Profile:
        lists = ("chrome", "busy", "approval", "auth", "trust", "error", "user", "assistant")
        return cls(
            name=name,
            label=d.get("label", name),
            verified=d.get("verified", "source"),
            commands=frozenset(d.get("commands") or []),
            command_patterns=_compile(d.get("command_patterns")),
            markers=_compile(d.get("markers")),
            title={k: re.compile(v) for k, v in (d.get("title") or {}).items()},
            done=_compile(d.get("done")),
            noise=_compile(d.get("noise")),
            continuation=_compile(d.get("continuation")),
            done_required=bool(d.get("done_required")),
            dialog_tail=int(d.get("dialog_tail", DIALOG_TAIL)),
            **{k: _compile(d.get(k)) for k in lists},
        )


@cache
def load_profiles(path: Path = PROFILES_PATH) -> dict[str, Profile]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))["profiles"]
    return {name: Profile.from_dict(name, d) for name, d in raw.items()}


def _any(patterns: list[re.Pattern[str]], line: str) -> bool:
    return any(p.search(line) for p in patterns)


def _last(patterns: list[re.Pattern[str]], lines: list[str]) -> int:
    """Index of the last line matching any pattern, or -1."""
    if patterns:
        for i in range(len(lines) - 1, -1, -1):
            if _any(patterns, lines[i]):
                return i
    return -1


def detect(
    pane_command: str | None,
    lines: list[str],
    shells: list[str],
    title: str = "",
    profiles: dict[str, Profile] | None = None,
) -> str:
    """Which tool owns the pane: a profile name, ``shell`` or ``terminal`` (any other program).

    A capture without a recorded command (old data, or a screen passed in by hand) is read as
    Claude Code, as it always was.
    """
    profs = profiles if profiles is not None else load_profiles()
    if pane_command in shells:
        return "shell"
    if pane_command:
        for name, p in profs.items():
            if pane_command in p.commands or any(r.match(pane_command) for r in p.command_patterns):
                return name
    for name, p in profs.items():
        if title and "marker" in p.title and p.title["marker"].search(title):
            return name
    best, best_at = None, -1
    for name, p in profs.items():
        at = _last(p.markers, lines)
        if at > best_at:
            best, best_at = name, at
    if best:
        return best
    return "claude" if pane_command is None else "terminal"


# ---------------------------------------------------------------- reading one screen


@dataclass
class Reading:
    """What a profile's strings say about one screen."""

    dialog: tuple[str, str] | None  # (kind, line): approval / auth / trust, live on screen
    error: str | None
    busy: str | None
    empty: bool  # no conversation yet
    who: str  # "assistant", "user" or "none"
    prompt: str  # the newest prompt a person submitted
    message: str  # what the agent said since that prompt


def _clean(line: str) -> str:
    """A conversation line without box borders (tool output is drawn in boxes)."""
    return BOX_RE.sub(" ", line).strip()


def read(p: Profile, lines: list[str], max_lines: int = 40) -> Reading:
    """``lines``: plain screen lines, top to bottom."""
    full = [x.rstrip() for x in lines]
    while full and not full[-1].strip():
        full.pop()
    chrome_at = _last(p.chrome, full)
    # the input area is at the bottom; a border far above the end is part of the conversation
    if chrome_at >= 0 and len([x for x in full[chrome_at:] if x.strip()]) > 12:
        chrome_at = -1

    # a dialog replaces the input area, so it is live only when no input area follows it
    dialog = None
    shown = [i for i, x in enumerate(full) if x.strip()][-p.dialog_tail :]
    start = shown[0] if shown else 0
    for kind, patterns in (("approval", p.approval), ("auth", p.auth), ("trust", p.trust)):
        at = _last(patterns, full[start:])
        if at >= 0 and start + at > chrome_at:
            # evidence: the question (the first matching line), not the option under it
            lo = max(start, chrome_at + 1)
            asked = next(x for x in full[lo:] if _any(patterns, x))
            dialog = (kind, _clean(asked))
            break

    body = full[:chrome_at] if chrome_at >= 0 else full
    body = [x for x in body if x.strip() and not _any(p.noise, x)]
    tail = [x for x in full if x.strip()][-BUSY_TAIL:]
    busy = next((x.strip() for x in reversed(tail) if _any(p.busy, x)), None)
    said = [x for x in body if not _any(p.busy, x)]

    # the newest submitted prompt, with the lines its text wrapped onto
    user_at = _last(p.user, said)
    after = said[user_at + 1 :] if user_at >= 0 else said
    prompt_lines = [said[user_at]] if user_at >= 0 else []
    n = 0
    if user_at >= 0:
        indent = _indent(said[user_at])
        while n < len(after) and (
            _any(p.continuation, after[n]) if p.continuation else _indent(after[n]) > indent + 1
        ):
            n += 1
    prompt_lines += after[:n]
    after = after[n:]  # everything the agent printed since: tool lines, answers, errors
    error = next((x.strip() for x in reversed(after[-ERROR_TAIL:]) if _any(p.error, x)), None)
    done_at = _last(p.done, after)
    finished = done_at >= 0
    if finished:
        after = after[:done_at]
    message = "\n".join([c for c in (_clean(x) for x in after) if c][-max_lines:])

    if user_at >= 0 and not finished and (p.done_required or not message):
        who = "user"  # the turn that prompt started has produced nothing, or has not ended
    elif message or finished:
        who = "assistant"
    else:
        who = "none"
    return Reading(
        dialog=dialog,
        error=error,
        busy=busy,
        empty=user_at < 0 and not finished and _last(p.assistant, said) < 0,
        who=who,
        prompt="\n".join(_clean(x) for x in prompt_lines),
        message=message,
    )


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())
