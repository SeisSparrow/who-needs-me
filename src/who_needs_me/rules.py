"""Layer 0: deterministic facts and rules over a tmux screen.

Everything here is a receipt computed by code, never something the screen text
claims about itself. Layer 1 (the decision model) only sees ``last_message`` and
the ``facts`` dict produced here.

Screen anatomy of Claude Code (from today's captures):

    ● assistant text …                      <- body; last "●" text block = last message
    ✶ Tinkering… (1m 42s · ↓ 9.2k tokens)   <- spinner: busy ("✻ Baked for 1s · done" = idle)
    ───────────────────────────────          <- input box border
    ❯ <typed text | ESC[2m ghost suggestion> <- input box
    ───────────────────────────────
      ⏵⏵ auto mode on · 1 shell · esc to interrupt   <- status line(s)
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import cache
from pathlib import Path

import yaml

from who_needs_me.redact import ANSI_RE, strip_ansi

CONFIG_PATH = Path(__file__).resolve().parents[2] / "factory.yaml"

SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
BORDER_RE = re.compile(r"^\s*─{20,}\s*$")
SPINNER_RE = re.compile(r"^\s*[·✢✳✶✻✽*]\s+\S+…\s+\(")
# "✻ Baked for 1m 2s · done 9:44 AM". In a very narrow pane the line wraps before "done"
# ("✻ Worked for 6m 28s ·" / "done 9:06 AM · 1 shell" / "still running"), so a line that starts
# at the left edge with the glyph and "<Word> for <duration>" is enough.
DONE_RE = re.compile(r"^\s*[·✢✳✶✻✽*]\s+\S+ for .*·\s*done\b|^[·✢✳✶✻✽*]\s+\S+ for \d+(?:h|m|s)\b")
# footer notices above the input box; in a narrow pane they are cut short ("new tas… · ✔ Update …")
NOISE_RE = re.compile(r"✔ Update|/restart to apply|^\s*new t\S*… ·|^\s*new task\?")
TOOL_CALL_RE = re.compile(r"^●\s+[A-Z][A-Za-z]*\(")
# notices Claude Code prints by itself with the same bullet as a message: a background task
# still running, the conversation compacted while idle. Nobody said them.
NOTICE_RE = re.compile(r"^\s*●\s+(?:Compacted while idle|Task \".*|Background (?:task|command) )")
USER_PROMPT_RE = re.compile(r"^[>❯]\s\S")
PEER_MSG_RE = re.compile(r"^›\s")  # a message delivered from another session, not typed by a person
SHELL_PROMPT_RE = re.compile(r"^(?:\S+@\S+:\S*\s?[$#]|[$#%])(?:\s.*)?$")
BACKGROUND_RE = re.compile(r"\b\d+\s+(?:shells?|background tasks?|bash)\b|\bbackground\b", re.I)
AUTH_ERROR_RE = re.compile(
    r"Please run /login|API Error: \d{3}|authentication_error|OAuth token (?:has )?expired"
    r"|Invalid API key|Not logged in",
    re.I,
)
TRUST_RE = re.compile(
    r"Yes, I trust this folder|Do you trust the files in this folder"
    r"|Is this a project you created or one you\s+trust"
)
BANNER_RE = re.compile(r"Claude Code v\d")
# the permission / question dialog: its question, and the key hints under its options
DIALOG_ASK_RE = re.compile(r"^\s*(?:Do you want to|Would you like to) .+\?\s*$")
DIALOG_RULE_RE = re.compile(r"^\s*╌{10,}\s*$")
DIALOG_FOOT_RE = re.compile(
    r"Esc to cancel · Tab to amend|Enter to select · .* to navigate|^\s*❯ 1\. Yes\b"
)
TS_FMT = "%Y%m%dT%H%M%SZ"


@cache
def load_config(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["layer0"]


# ---------------------------------------------------------------- screen parsing


@dataclass
class Parsed:
    body: list[str]  # plain lines above the input box (or the whole screen if no box)
    box: list[str]  # raw (ANSI) lines inside the input box
    status: list[str]  # plain lines below the input box
    has_box: bool


def parse(screen: str) -> Parsed:
    raw = screen.rstrip("\n").split("\n")
    # Claude Code draws its bullet as ● on Linux and ⏺ on macOS
    plain = [strip_ansi(line).replace("⏺", "●") for line in raw]
    borders = [i for i, line in enumerate(plain) if BORDER_RE.match(line)]
    # the input box is the last pair of borders whose first inner line starts with ❯
    for lo, hi in reversed(list(zip(borders, borders[1:], strict=False))):
        if hi > lo + 1 and plain[lo + 1].lstrip().startswith("❯"):
            return Parsed(plain[:lo], raw[lo + 1 : hi], plain[hi + 1 :], True)
    return Parsed(plain, [], [], False)


def split_dim(line: str) -> tuple[str, str]:
    """Split an ANSI line into (normal text, dim text). Dim = SGR 2, ended by 0/22."""
    normal: list[str] = []
    dim_text: list[str] = []
    dim = False
    pos = 0
    for m in ANSI_RE.finditer(line):
        (dim_text if dim else normal).append(line[pos : m.start()])
        pos = m.end()
        sgr = SGR_RE.fullmatch(m.group())
        if not sgr:
            continue
        params = sgr.group(1).split(";") if sgr.group(1) else ["0"]
        i = 0
        while i < len(params):
            p = params[i]
            if p in ("38", "48", "58"):  # extended color: skip its arguments
                i += 3 if i + 1 < len(params) and params[i + 1] == "5" else 5
                continue
            if p == "2":
                dim = True
            elif p in ("0", "22", ""):
                dim = False
            i += 1
    (dim_text if dim else normal).append(line[pos:])
    return "".join(normal), "".join(dim_text)


def _without_status(lines: list[str], keep_blank: bool) -> list[str]:
    """Drop update notices, spinner lines and finished-turn lines.

    In a narrow pane the finished-turn line wraps ("… · 2 shells still" / "running"). The
    leftover is part of that volatile line, not something the session said, so lines after a
    spinner or finished-turn line are dropped until the next block starts."""
    out: list[str] = []
    in_status = False
    for line in lines:
        if NOISE_RE.search(line):
            continue
        if SPINNER_RE.match(line) or DONE_RE.match(line) or NOTICE_RE.match(line):
            in_status = True
            continue
        if not line.strip():
            if keep_blank and not in_status:
                out.append(line)
            continue
        t = line.lstrip()
        if in_status and not (t.startswith("●") or PEER_MSG_RE.match(t) or _is_prompt(line)):
            continue
        in_status = False
        out.append(line)
    return out


def _meaningful(lines: list[str]) -> list[str]:
    """Body lines that carry content: no blanks, no status lines or their wrapped leftovers."""
    return _without_status(lines, keep_blank=False)


def last_message(body: list[str], max_lines: int = 40) -> str:
    """The last assistant text block: from the last non-tool ● line to the next ● or prompt.

    Falls back to the last ``max_lines`` meaningful lines (shell screens, dialogs).
    """
    lines = _without_status(body, keep_blank=True)
    start = None
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].lstrip()
        if s.startswith("●") and not TOOL_CALL_RE.match(s):
            start = i
            break
    if start is None:
        block = _meaningful(lines)[-max_lines:]
        return "\n".join(line.rstrip() for line in block)
    block = [lines[start].lstrip()[1:].strip()]
    for line in lines[start + 1 :]:
        s = line.lstrip()
        if s.startswith("●") or USER_PROMPT_RE.match(s):
            break
        block.append(line[2:].rstrip() if line.startswith("  ") else line.rstrip())
    while block and not block[-1].strip():
        block.pop()
    return "\n".join(block[-max_lines:])


def who_spoke_last(body: list[str]) -> tuple[str, str]:
    """Who has the last word on the screen, and the text of the newest prompt a person typed.

    Returns (who, prompt): who is "assistant", "user", "peer" or "none".

    A person's reply to a request shows up as a prompt block below it; reading the request
    again as if it were still open is the mistake this prevents. Read from the bottom:
    a finished-turn line ("… · done 9:44 AM") or a ● line means the assistant spoke last, a
    ❯ line means a person did, a › line means another session did. If the window holds
    nothing but unmarked text with no finished-turn line under it, that text is a long
    prompt that was just submitted (its ❯ line has scrolled off), not the assistant's.
    Slash commands (/clear, /model) are not prompts to the agent.
    """
    lines = [x for x in body if x.strip() and not NOISE_RE.search(x) and not SPINNER_RE.match(x)]

    def prompt_block(i: int) -> str:
        out = [lines[i].lstrip()[1:].strip()]
        for x in lines[i + 1 :]:
            t = x.lstrip()
            if t.startswith("●") or DONE_RE.match(x) or PEER_MSG_RE.match(t) or _is_prompt(x):
                break
            out.append(x.strip())
        return "\n".join(out)

    newest_prompt = ""
    for i in range(len(lines) - 1, -1, -1):
        if _is_prompt(lines[i]) and not lines[i].lstrip()[1:].strip().startswith("/"):
            newest_prompt = prompt_block(i)
            break
    for i in range(len(lines) - 1, -1, -1):
        x = lines[i]
        t = x.lstrip()
        if DONE_RE.match(x) or t.startswith("●"):
            return "assistant", newest_prompt
        if PEER_MSG_RE.match(t):
            return "peer", newest_prompt
        if _is_prompt(x):
            if t[1:].strip().startswith("/"):
                return "none", newest_prompt
            return "user", newest_prompt
    if lines and not any(BANNER_RE.search(x) for x in lines):
        return "user", "\n".join(x.strip() for x in lines)
    return "none", newest_prompt


def _is_prompt(line: str) -> bool:
    """A submitted prompt starts at the left edge; "> quote" inside a message is indented."""
    return len(line) - len(line.lstrip()) <= 1 and bool(USER_PROMPT_RE.match(line.lstrip()))


# whitespace and box-drawing characters: both move when a pane is resized and its text re-wraps
_WRAP_NOISE = re.compile(r"[\s\u2500-\u257f]+")
# a 52x23 pane shows about 340 characters of conversation and a 25x23 one about 200, and what is
# above the window is gone after a resize, so the tail has to be shorter than the smallest pane
# in real use
KEY_TAIL_CHARS = 120


def content_key(screen: str) -> str:
    """Identity of what the session is showing now: the end of the conversation, with volatile
    parts (spinner timers, input box, status) removed, so a session that only ticks its timer
    still counts as unchanged.

    It must also survive the pane being resized (attaching from a different terminal re-wraps
    every line and changes how much history is on screen). So line breaks, spacing and table
    borders are dropped, and only the last ``KEY_TAIL_CHARS`` characters count: the end of the
    conversation is on screen at any size, the top of the window is not."""
    text = _WRAP_NOISE.sub("", "\n".join(_meaningful(parse(screen).body)))
    return hashlib.sha256(text[-KEY_TAIL_CHARS:].encode()).hexdigest()


def stale_minutes(series: list[tuple[str, str]]) -> int:
    """Minutes the screen has been unchanged, from (ts, content_key) pairs in time order."""
    if not series:
        return 0
    last_ts, key = series[-1]
    first_ts = last_ts
    for ts, k in reversed(series):
        if k != key:
            break
        first_ts = ts
    delta = datetime.strptime(last_ts, TS_FMT) - datetime.strptime(first_ts, TS_FMT)
    return int(delta.total_seconds() // 60)


# ---------------------------------------------------------------- rules


@dataclass
class Hit:
    rule: str
    state: str | None  # None = informational, does not decide the state
    reason: str
    evidence: str
    tier: str | None = None  # needs_human only: "red" or "yellow"; None = red


@dataclass
class Layer0:
    facts: dict
    hits: list[Hit]
    last_message: str
    evidence_default: str = ""
    config: dict = field(default_factory=dict, repr=False)

    def verdict(self) -> Hit | None:
        """The decisive hit by configured precedence, or None if layer 1 must decide."""
        order = self.config.get("precedence", [])
        decisive = [h for h in self.hits if h.state is not None]
        decisive.sort(key=lambda h: order.index(h.rule) if h.rule in order else len(order))
        return decisive[0] if decisive else None

    def to_dict(self) -> dict:
        return {
            "facts": self.facts,
            "hits": [asdict(h) for h in self.hits],
            "last_message": self.last_message,
        }


def _first(pattern: re.Pattern[str], lines: list[str]) -> str | None:
    for line in lines:
        if pattern.search(line):
            return line.strip()
    return None


def rule_dropped_to_shell(pane_command: str | None, p: Parsed, cfg: dict) -> Hit | None:
    if pane_command is not None:
        if pane_command in cfg["shell_commands"]:
            return Hit(
                "dropped_to_shell", "dropped_to_shell", "none", f"pane_command={pane_command}"
            )
        return None
    # no receipt: fall back to the screen (no input box, last line is a shell prompt)
    tail = _meaningful(p.body)
    if not p.has_box and tail and SHELL_PROMPT_RE.match(tail[-1].strip()):
        return Hit("dropped_to_shell", "dropped_to_shell", "none", tail[-1].strip())
    return None


def rule_auth_error(p: Parsed) -> Hit | None:
    # in the conversation, or in the status line under the input box ("Not logged in · Run /login")
    line = _first(AUTH_ERROR_RE, _meaningful(p.body)[-8:])
    if not line:
        found = next((m for m in (AUTH_ERROR_RE.search(x) for x in p.status) if m), None)
        line = found.string[found.start() :].strip() if found else None
    return Hit("auth_error", "needs_human", "machine", line) if line else None


def rule_approval_dialog(p: Parsed) -> Hit | None:
    """Claude Code's own "may I run this?" dialog. It replaces the input box, so it only
    counts when there is no input box. (Sessions in auto mode never show it.)"""
    if p.has_box:
        return None
    tail = _meaningful(p.body)[-14:]
    if not _first(DIALOG_FOOT_RE, tail):
        return None
    line = _first(DIALOG_ASK_RE, tail) or _first(DIALOG_FOOT_RE, tail)
    # the command or file it asks about is shown between two dashed rules
    rules_at = [i for i, x in enumerate(tail) if DIALOG_RULE_RE.match(x)]
    if len(rules_at) >= 2 and rules_at[1] > rules_at[0] + 1:
        line = f"{line} {tail[rules_at[0] + 1].strip()[:120]}"
    return Hit("approval_dialog", "needs_human", "approval", line)


def rule_trust_prompt(p: Parsed) -> Hit | None:
    line = _first(TRUST_RE, p.body)
    return Hit("trust_prompt", "needs_human", "machine", line) if line else None


def rule_busy(p: Parsed) -> Hit | None:
    line = _first(SPINNER_RE, [x for x in p.body if x.strip()][-4:])
    line = line or _first(re.compile(r"esc to interrupt"), p.status)
    return Hit("busy", "busy", "none", line) if line else None


def rule_prompt_submitted(p: Parsed, who: str, prompt: str) -> Hit | None:
    """Someone has just given the session input and it has not answered yet: it is working,
    or about to. Whatever it asked before has been answered."""
    if not p.has_box or who not in ("user", "peer"):
        return None
    first = next((x for x in prompt.split("\n") if x.strip()), "") if who == "user" else ""
    return Hit("prompt_submitted", "busy", "none", f"❯ {first[:100]}" if first else "new input")


def rule_empty_session(p: Parsed) -> Hit | None:
    """Freshly started session: welcome banner, input box, and no conversation at all
    (no assistant "●" line, no user prompt). Input-box text does not count."""
    if not p.has_box:
        return None
    banner = _first(BANNER_RE, p.body)
    talk = any(x.lstrip().startswith("●") or USER_PROMPT_RE.match(x.lstrip()) for x in p.body)
    if banner and not talk:
        return Hit("empty_session", "idle_done", "none", banner)
    return None


def rule_tripwire(message: str, cfg: dict, width: int | None = None) -> Hit | None:
    """Keyword hit in the tail of the last message; the latest matching line wins.

    The tail is counted in lines of a pane about ``TAIL_REFERENCE_WIDTH`` columns wide. A
    narrower pane wraps the same sentences into more lines, so the count grows with it;
    otherwise resizing a window would change what is flagged."""
    n = cfg["tripwire_tail_lines"]
    if width and width < TAIL_REFERENCE_WIDTH:
        n *= -(-TAIL_REFERENCE_WIDTH // width)
    tail = [x for x in message.split("\n") if x.strip()][-n:]
    for line in reversed(tail):
        for reason, patterns in cfg["tripwire"].items():
            if any(re.search(pat, line, re.I) for pat in patterns):
                return Hit("tripwire", "needs_human", reason, line.strip())
    return None


TAIL_REFERENCE_WIDTH = 100


def _lines(message: str) -> list[str]:
    return [x.strip() for x in message.split("\n") if x.strip()]


def approval_line(message: str, cfg: dict) -> str | None:
    """Latest line of the message with a "waiting for approval" cue."""
    cues = cfg.get("approval_cues", [])
    for line in reversed(_lines(message)):
        if any(re.search(c, line, re.I) for c in cues):
            return line
    return None


def cue_near(message: str, line: str, cfg: dict) -> bool:
    """Is there an approval cue on this line or an adjacent one?"""
    lines = _lines(message)
    cues = cfg.get("approval_cues", [])
    for i, x in enumerate(lines):
        if x == line.strip():
            near = lines[max(0, i - 1) : i + 2]
            if any(re.search(c, n, re.I) for c in cues for n in near):
                return True
    return False


def reason_line(message: str, reason: str, cfg: dict) -> str | None:
    """Latest line of the message that mentions this reason (tripwire vocabulary), used as
    evidence when the model, not a rule, raised needs_human. Code picks the line; the
    model never quotes text."""
    for line in reversed([x for x in message.split("\n") if x.strip()]):
        if any(re.search(pat, line, re.I) for pat in cfg["tripwire"].get(reason, [])):
            return line.strip()
    return None


def rule_ghost_text(p: Parsed) -> tuple[Hit | None, str, str]:
    """Returns (hit, typed text, ghost text). Ghost text is dropped; typed text is kept."""
    typed, ghost = [], []
    for line in p.box:
        normal, dim = split_dim(line)
        typed.append(normal.replace("❯", "", 1).replace("\xa0", " ").strip())
        ghost.append(dim.strip())
    typed_s = "\n".join(t for t in typed if t)
    ghost_s = "\n".join(g for g in ghost if g)
    hit = Hit("ghost_text", None, "none", ghost_s) if ghost_s else None
    return hit, typed_s, ghost_s


def rule_stale_background(stale: int | None, background: bool, cfg: dict) -> Hit | None:
    if stale is not None and background and stale >= cfg["stale_minutes_threshold"]:
        return Hit("stale_background", None, "none", f"unchanged {stale} min, background task")
    return None


def stale_bucket(stale: int | None) -> str:
    if stale is None:
        return "unknown"
    for limit, name in ((5, "under_5_min"), (15, "5_to_15_min"), (30, "15_to_30_min")):
        if stale < limit:
            return name
    return "over_30_min"


def evaluate(
    screen: str,
    pane_command: str | None = None,
    stale: int | None = None,
    config: dict | None = None,
    meta: dict | None = None,
) -> Layer0:
    """Run all enabled layer-0 rules on one screen (ANSI text, pane command line removed).

    ``meta`` holds what tmux reported with the capture (``Screen.meta()``): pane width,
    alternate screen, terminal title, hook note. All of it is optional.

    Which rules run depends on what owns the pane: Claude Code (the rules in this file),
    another agent (``profiles.py``), or a plain program or shell (``terminal.py`` first)."""
    from who_needs_me import profiles as pf
    from who_needs_me import terminal

    cfg = config or load_config()
    meta = meta or {}
    on = cfg["rules"]
    plain = [strip_ansi(x) for x in screen.rstrip("\n").split("\n")]
    tool = pf.detect(pane_command, plain, cfg["shell_commands"], meta.get("title") or "")
    first: list[Hit] = []
    if tool in ("shell", "terminal"):
        wait = terminal.blocked(plain, pane_command, meta.get("alternate"))
        if wait and on.get("blocked_input", True):
            first.append(Hit("blocked_input", "needs_human", "input", wait.summary, wait.tier))
        elif (
            tool == "terminal"
            and on.get("foreground_program", True)
            and (stale is None or stale < cfg.get("foreground_quiet_minutes", 10))
        ):
            # a program is in the foreground and its output still moves: it is working
            first.append(Hit("foreground_program", "busy", "none", f"running {pane_command}"))
    if tool in ("claude", "shell", "terminal"):
        l0 = _evaluate_claude(screen, pane_command, stale, cfg, meta)
    else:
        l0 = _evaluate_profile(pf.load_profiles()[tool], plain, pane_command, stale, cfg, meta)
    l0.hits = first + l0.hits + _receipts(tool, meta, stale, on)
    l0.facts["tool"] = tool
    return l0


def _receipts(tool: str, meta: dict, stale: int | None, on: dict) -> list[Hit]:
    """What the tool itself reported: its terminal title and the note its hooks left."""
    from who_needs_me import profiles as pf

    hits = []
    prof = pf.load_profiles().get(tool)
    title = meta.get("title") or ""
    if prof and title and on.get("title", True):
        if "approval" in prof.title and prof.title["approval"].search(title):
            hits.append(Hit("title_approval", "needs_human", "approval", f"title: {title[:80]}"))
        elif "busy" in prof.title and prof.title["busy"].search(title):
            hits.append(Hit("title_busy", "busy", "none", f"title: {title[:80]}"))
    note = hook_note(meta.get("hook") or "")
    if note and on.get("hooks", True) and tool not in ("shell", "terminal"):
        by = f"{note['tool']} hook: {note['event']}"
        if note["kind"] == "approval":
            hits.append(Hit("hook_approval", "needs_human", "approval", by))
        elif (
            note["kind"] == "working"
            and (prof is None or prof.verified != "real")
            and (stale is None or stale < HOOK_WORKING_MAX_MINUTES)
        ):
            # An interrupted turn fires no hook, so "working" can be out of date. It is used
            # only for tools whose busy line has not been checked on a real screen, and only
            # while the screen still moves.
            hits.append(Hit("hook_working", "busy", "none", by))
    return hits


HOOK_WORKING_MAX_MINUTES = 5


def hook_note(text: str) -> dict | None:
    """Parse the pane note written by ``hook.py``: "<unix time> <tool> <kind> <event>"."""
    parts = text.split(" ", 3)
    if len(parts) != 4 or not parts[0].isdigit():
        return None
    return {"at": int(parts[0]), "tool": parts[1], "kind": parts[2], "event": parts[3]}


def _facts(pane_command, stale, who, prompt, errors, **extra) -> dict:
    return {
        "pane_command": pane_command or "unknown",
        "input_box": extra.get("input_box", False),
        "background_task": "running" if extra.get("background") else "none",
        "screen_unchanged": stale_bucket(stale),
        "stale_minutes": stale,
        "ghost_text_ignored": bool(extra.get("ghost")),
        "user_typing": bool(extra.get("typed")),
        "last_error_line": errors[-1].strip() if errors else "",
        "who_spoke_last": who,
        "last_prompt_key": hashlib.sha256(prompt.encode()).hexdigest()[:12] if prompt else "",
    }


def _evaluate_profile(prof, plain, pane_command, stale, cfg, meta) -> Layer0:
    """Another agent's screen, read with its profile; same hits and facts as Claude Code's."""
    from who_needs_me import profiles as pf

    on = cfg["rules"]
    r = pf.read(prof, plain, cfg["last_message_max_lines"])
    hits: list[Hit | None] = []
    if r.dialog:
        kind, line = r.dialog
        if kind == "approval" and on.get("approval_dialog", True):
            hits.append(Hit("approval_dialog", "needs_human", "approval", line))
        elif kind == "auth" and on.get("auth_error"):
            hits.append(Hit("auth_error", "needs_human", "machine", line))
        elif kind == "trust" and on.get("trust_prompt"):
            hits.append(Hit("trust_prompt", "needs_human", "machine", line))
    if r.error and on.get("auth_error"):
        hits.append(Hit("auth_error", "needs_human", "machine", r.error))
    if r.busy and on.get("busy"):
        hits.append(Hit("busy", "busy", "none", r.busy))
    if r.empty and on.get("empty_session"):
        hits.append(Hit("empty_session", "idle_done", "none", f"{prof.label}: no conversation"))
    if r.who == "user" and on.get("prompt_submitted"):
        first = next((x for x in r.prompt.split("\n") if x.strip()), "")
        hits.append(Hit("prompt_submitted", "busy", "none", first[:100] or "new input"))
    msg = "" if r.who == "user" else r.message
    if on.get("tripwire"):
        hits.append(rule_tripwire(msg, cfg, meta.get("width")))
    errors = [x for x in msg.split("\n")[-8:] if re.search(r"(?i)\berror\b|failed", x)]
    facts = _facts(pane_command, stale, r.who, r.prompt, errors, input_box=not r.dialog)
    last_line = next((x for x in reversed(msg.split("\n")) if x.strip()), "")
    return Layer0(facts, [h for h in hits if h], msg, last_line.strip(), cfg)


def _evaluate_claude(
    screen: str, pane_command: str | None, stale: int | None, cfg: dict, meta: dict
) -> Layer0:
    """Claude Code's screen (also the fallback for a shell or an unknown program)."""
    on = cfg["rules"]
    p = parse(screen)
    who, prompt = who_spoke_last(p.body) if p.has_box else ("none", "")
    # an answered request is no longer the session's last word
    msg = "" if who in ("user", "peer") else last_message(p.body, cfg["last_message_max_lines"])
    background = bool(_first(BACKGROUND_RE, p.status))

    hits: list[Hit | None] = []
    if on.get("dropped_to_shell"):
        hits.append(rule_dropped_to_shell(pane_command, p, cfg))
    if on.get("auth_error"):
        hits.append(rule_auth_error(p))
    if on.get("trust_prompt"):
        hits.append(rule_trust_prompt(p))
    if on.get("approval_dialog", True):
        hits.append(rule_approval_dialog(p))
    if on.get("busy"):
        hits.append(rule_busy(p))
    if on.get("empty_session"):
        hits.append(rule_empty_session(p))
    if on.get("prompt_submitted"):
        hits.append(rule_prompt_submitted(p, who, prompt))
    if on.get("tripwire"):
        hits.append(rule_tripwire(msg, cfg, meta.get("width")))
    typed, ghost = "", ""
    if on.get("ghost_text"):
        ghost_hit, typed, ghost = rule_ghost_text(p)
        hits.append(ghost_hit)
    if on.get("stale_background"):
        hits.append(rule_stale_background(stale, background, cfg))

    errors = [x for x in _meaningful(p.body)[-8:] if re.search(r"(?i)\berror\b|failed", x)]
    facts = _facts(
        pane_command,
        stale,
        who,
        prompt,
        errors,
        input_box=p.has_box,
        background=background,
        ghost=ghost,
        typed=typed,
    )
    last_line = next((x for x in reversed(msg.split("\n")) if x.strip()), "")
    return Layer0(facts, [h for h in hits if h], msg, last_line.strip(), cfg)
