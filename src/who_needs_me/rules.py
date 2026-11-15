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
DONE_RE = re.compile(r"^\s*[·✢✳✶✻✽*]\s+\S+ for .*·\s*done\b")
NOISE_RE = re.compile(r"✔ Update installed|/restart to apply")
TOOL_CALL_RE = re.compile(r"^●\s+[A-Z][A-Za-z]*\(")
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
    plain = [strip_ansi(line) for line in raw]
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


def _meaningful(lines: list[str]) -> list[str]:
    """Body lines minus spinner/done/update-notice lines and blanks."""
    return [
        line
        for line in lines
        if line.strip()
        and not SPINNER_RE.match(line)
        and not DONE_RE.match(line)
        and not NOISE_RE.search(line)
    ]


def last_message(body: list[str], max_lines: int = 40) -> str:
    """The last assistant text block: from the last non-tool ● line to the next ● or prompt.

    Falls back to the last ``max_lines`` meaningful lines (shell screens, dialogs).
    """
    lines = [line for line in body if not NOISE_RE.search(line)]
    lines = [line for line in lines if not SPINNER_RE.match(line) and not DONE_RE.match(line)]
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


def content_key(screen: str) -> str:
    """Hash of the screen with volatile parts (spinner timers, input box, status) removed,
    so a session that only ticks its timer still counts as unchanged."""
    return hashlib.sha256("\n".join(_meaningful(parse(screen).body)).encode()).hexdigest()


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
    line = _first(AUTH_ERROR_RE, _meaningful(p.body)[-8:])
    return Hit("auth_error", "needs_human", "machine", line) if line else None


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


def rule_tripwire(message: str, cfg: dict) -> Hit | None:
    """Keyword hit in the tail of the last message; the latest matching line wins."""
    tail = [x for x in message.split("\n") if x.strip()][-cfg["tripwire_tail_lines"] :]
    for line in reversed(tail):
        for reason, patterns in cfg["tripwire"].items():
            if any(re.search(pat, line, re.I) for pat in patterns):
                return Hit("tripwire", "needs_human", reason, line.strip())
    return None


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
) -> Layer0:
    """Run all enabled layer-0 rules on one screen (ANSI text, pane command line removed)."""
    cfg = config or load_config()
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
    if on.get("busy"):
        hits.append(rule_busy(p))
    if on.get("empty_session"):
        hits.append(rule_empty_session(p))
    if on.get("prompt_submitted"):
        hits.append(rule_prompt_submitted(p, who, prompt))
    if on.get("tripwire"):
        hits.append(rule_tripwire(msg, cfg))
    typed, ghost = "", ""
    if on.get("ghost_text"):
        ghost_hit, typed, ghost = rule_ghost_text(p)
        hits.append(ghost_hit)
    if on.get("stale_background"):
        hits.append(rule_stale_background(stale, background, cfg))

    errors = [x for x in _meaningful(p.body)[-8:] if re.search(r"(?i)\berror\b|failed", x)]
    facts = {
        "pane_command": pane_command or "unknown",
        "input_box": p.has_box,
        "background_task": "running" if background else "none",
        "screen_unchanged": stale_bucket(stale),
        "stale_minutes": stale,
        "ghost_text_ignored": bool(ghost),
        "user_typing": bool(typed),
        "last_error_line": errors[-1].strip() if errors else "",
        "who_spoke_last": who,
        "last_prompt_key": hashlib.sha256(prompt.encode()).hexdigest()[:12] if prompt else "",
    }
    last_line = next((x for x in reversed(msg.split("\n")) if x.strip()), "")
    return Layer0(facts, [h for h in hits if h], msg, last_line.strip(), cfg)
