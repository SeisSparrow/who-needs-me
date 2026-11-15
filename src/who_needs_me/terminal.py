"""A terminal that is waiting for a person, whatever is running in it.

A script stops at "[Y/n]", sudo asks for a password, ssh asks whether to trust a host, git
wants a username, a pager or an editor is open. None of this depends on which agent, if any,
is in the pane. The check looks at where the cursor waits (the last line with text) and at
the two receipts tmux gives for free: the foreground program and whether a full-screen
program owns the pane.

Kinds, most urgent first:
  secret    a password, passphrase, username or one-time code is being asked for   (red)
  confirm   a yes/no, "press a key" or "enter a value" question                     (red)
  editor    an editor is open (git commit, crontab -e)                              (yellow)
  pager     a pager is open (less, man, git log)                                    (yellow)

The strings come from real screens (tests/fixtures/terminal/), from the programs named in
the comments, or both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SECRET = [
    r"(?i)^(?:\[sudo\] )?password(?: for [^:]+)?:\s*$",  # sudo, su, login
    r"(?i)^\S+@\S+'s password:\s*$",  # ssh
    r"(?i)^enter passphrase(?: for .+)?:\s*$",  # ssh, ssh-keygen, gpg
    r"(?i)^(?:username|password) for '.+':\s*$",  # git over https
    r"(?i)^(?:verification code|one-time (?:password|code)|otp|mfa code|2fa code)\b.*:\s*$",
    r"(?i)^enter (?:your )?(?:password|passphrase|pin|token|otp|mfa code)\b.*:\s*$",
    r"(?i)^(?:new|current|old|retype|confirm)(?: new)? (?:unix )?password:\s*$",  # passwd
    r"(?i)^enter (?:pin|passcode) for ",  # smart cards, security keys
]
CONFIRM = [
    r"\[[Yy](?:es)?/[Nn](?:o)?(?:/[^\]]+)?\]\s*[:?]?\s*$",  # [Y/n] apt, [y/N] dnf, [yes/no]
    r"\([Yy](?:es)?/[Nn](?:o)?\)\s*[:?]?\s*$",  # (y/n) (yes/no)
    r"\(yes/no/\[fingerprint\]\)\?\s*$",  # ssh: unknown host
    r"(?i)^ok to proceed\? \(y\)\s*$",  # npx
    r"(?i)^proceed \(\[?y\]?/n\)\?\s*$",  # conda, pip uninstall
    r"\(y/N/\?\)|\[Y/n/\?\]|\[y/n/[a-z?,/]+\]\??\s*$",  # git add -p, pacman
    r"(?i)press (?:any key|enter|return|\[enter\])(?: key)? to (?:continue|proceed|exit|close)",
    r"(?i)^\s*enter a value:\s*$",  # terraform
    r"(?i)^(?:do you want to|are you sure you want to|would you like to) .+\?\s*$",
    r"(?i)\b(?:overwrite|replace|remove|delete|continue|proceed)\b[^.]*\?\s*(?:\[[^\]]+\]|\([^)]+\))\s*:?\s*$",
]
PAGER_LINE = [
    r"^:\s*$",  # less, man
    r"^\(END\)\s*$",
    r"^--More--",
    r"^lines \d+-\d+(?:/\d+)?(?: \(END\)| \d+%)?\s*$",  # systemd / git pagers
    r"^\S.*\(press h for help or q to quit\)\s*$",
    r"^Manual page .+ line \d+",
]
EDITOR_LINE = [
    r"^-- (?:INSERT|REPLACE|VISUAL(?: LINE| BLOCK)?) --",  # vim
    r'^(?:<|")\S.*"? \d+L, \d+B\s*$',  # vim, after opening a file
    r"^\^G (?:Get )?Help\s+\^O ",  # nano / pico, first help line
    r"^\^X Exit\s+\^[JR] ",  # nano / pico, second help line
    r"^-UUU?:[-*%]{2}",  # emacs mode line
]
PAGERS = frozenset({"less", "more", "most", "man", "pg", "bat", "moar", "delta"})
EDITORS = frozenset(
    {"vi", "vim", "nvim", "view", "vimdiff", "nano", "pico", "emacs", "micro", "hx", "helix"}
    | {"ed", "joe", "kak", "ne", "mcedit", "crontab", "visudo"}
)
# full-screen programs that are simply running, not waiting for anyone
WATCHERS = frozenset({"top", "htop", "btop", "watch", "tail", "nvtop", "glances", "iotop", "k9s"})

_C = {
    name: [re.compile(p) for p in pats]
    for name, pats in (
        ("secret", SECRET),
        ("confirm", CONFIRM),
        ("pager", PAGER_LINE),
        ("editor", EDITOR_LINE),
    )
}


@dataclass
class Blocked:
    kind: str  # secret | confirm | editor | pager
    line: str  # the line that shows it
    tier: str  # red | yellow

    @property
    def summary(self) -> str:
        what = {
            "secret": "asking for a password or code",
            "confirm": "asking a question",
            "editor": "an editor is open",
            "pager": "a pager is open",
        }[self.kind]
        return f"{what}: {self.line[:120]}" if self.line else what


def blocked(
    lines: list[str], pane_command: str | None = None, alternate: bool | None = None
) -> Blocked | None:
    """Is this terminal waiting for a person to type? ``lines``: plain screen lines.

    Only the last line with text can be a question the cursor is waiting on; for editors,
    whose bottom lines are a status bar and help, the last three are read."""
    text = [x.rstrip() for x in lines if x.strip()]
    if not text or pane_command in WATCHERS:
        return None
    last = text[-1].strip()
    for kind in ("secret", "confirm"):
        if any(p.search(last) for p in _C[kind]):
            return Blocked(kind, last, "red")
    if pane_command in EDITORS:
        return Blocked("editor", f"{pane_command} is open", "yellow")
    if pane_command in PAGERS:
        return Blocked("pager", f"{pane_command} is open", "yellow")
    # the program is hidden behind another name (git starts vim, man starts less)
    if alternate is not False:
        for x in reversed(text[-3:]):
            if any(p.search(x.strip()) for p in _C["editor"]):
                return Blocked("editor", x.strip(), "yellow")
        if any(p.search(last) for p in _C["pager"]):
            return Blocked("pager", last, "yellow")
    return None
