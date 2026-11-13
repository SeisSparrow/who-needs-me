"""Redact raw tmux screen captures before they enter the repo.

Removes internal IPs, hostnames, emails, tokens and usernames from both the plain
`.txt` capture and the colored `.ansi` capture. In `.ansi` files, color escape codes
are kept intact so the ghost-text rule (layer 0) still has its signal.

Raw screens are read from a directory outside the repo (``--raw`` or ``WNM_RAW_DIR``).
Output goes to ``data/screens/``. Every run rebuilds the output from scratch, so a
newly learned username also gets scrubbed from older screens.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")

Repl = str | Callable[[re.Match[str]], str]

# Order matters: specific token shapes first, generic shapes last.
RULES: list[tuple[str, re.Pattern[str], Repl]] = [
    (
        "token",
        re.compile(
            r"(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}"
            r"|AIza[0-9A-Za-z_-]{30,}"
            r"|gh[pousr]_[A-Za-z0-9]{20,}"
            r"|github_pat_[A-Za-z0-9_]{20,}"
            r"|xox[abprs]-[A-Za-z0-9-]{10,}"
            r"|AKIA[0-9A-Z]{16}"
            r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,})"
        ),
        "<token>",
    ),
    ("bearer", re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1<token>"),
    (
        "secret_kv",
        re.compile(
            # (?<![A-Za-z0-9]) instead of \b so GEMINI_API_KEY=... matches
            r"(?i)(?<![A-Za-z0-9])([A-Za-z0-9_-]*?(?:api[_-]?key|token|secret|password|passwd"
            r"|authorization)[\"']?\s*[:=]\s*[\"']?)(?!bearer\s|<token>)[^\s\"',}]{6,}"
        ),
        r"\1<token>",
    ),
    ("long_hex", re.compile(r"\b[0-9a-fA-F]{40,}\b"), "<hex>"),
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
        "<email>",
    ),
    ("ipv4", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"), "<ip>"),
    (
        "internal_host",
        re.compile(
            r"\b[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:internal|local|lan|corp|intranet|localdomain)\b"
        ),
        "<host>",
    ),
    ("cloud_host", re.compile(r"\bip-\d{1,3}-\d{1,3}-\d{1,3}-\d{1,3}\b"), "<host>"),
    # Azure resource names: storage account after the CLI flag, resource groups named rg-*
    (
        "azure_storage",
        re.compile(r"(--(?:storage-)?account(?:-name)?[ =]+)(?!<)[A-Za-z0-9]{3,24}\b"),
        r"\1<storage-account>",
    ),
    (
        "azure_rg",
        re.compile(r"(?<![A-Za-z0-9<-])rg-[A-Za-z0-9_]+(?:[-.][A-Za-z0-9_]+)*"),
        "<resource-group>",
    ),
    ("prompt", re.compile(r"\b[a-z_][a-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*(?=:)"), "user@host"),
    ("home_path", re.compile(r"(/home/|/Users/)(?!user\b)[^/\s]+"), r"\1user"),
    # Claude Code encodes paths as e.g. /tmp/claude-1000/-home-alice-projects-x/
    ("home_slug", re.compile(r"(-home-|-Users-)(?!user-)[A-Za-z0-9_.]+?(?=-)"), r"\1user"),
]

# Patterns that harvest identifiers to scrub everywhere (including truncated mentions).
_USER_HARVEST = [
    re.compile(r"/home/([^/\s]+)"),
    re.compile(r"/Users/([^/\s]+)"),
    re.compile(r"-home-([A-Za-z0-9_.]+?)-"),
    re.compile(r"\b([a-z_][a-z0-9_.-]*)@[A-Za-z0-9][A-Za-z0-9.-]*:"),
]
_HOST_HARVEST = [re.compile(r"\b[a-z_][a-z0-9_.-]*@([A-Za-z0-9][A-Za-z0-9.-]*):")]
_NOT_SECRET = {"user", "host", "root", "shared", "runner"}


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def escape_payloads(s: str) -> str:
    """Text hidden inside escape sequences, e.g. OSC 8 hyperlink URLs (file:///home/...)."""
    return "\n".join(m.group() for m in ANSI_RE.finditer(s) if m.group().startswith("\x1b]"))


def sub_preserving_ansi(s: str, pattern: re.Pattern[str], repl: Repl) -> str:
    """Like ``pattern.sub(repl, s)`` but matches against the text with escapes removed.

    Escape sequences that fall inside a match are re-emitted after the replacement,
    so the color state of the rest of the line does not change.
    """
    idx: list[int] = []
    pos = 0
    for m in ANSI_RE.finditer(s):
        idx.extend(range(pos, m.start()))
        pos = m.end()
    idx.extend(range(pos, len(s)))
    plain = "".join(s[i] for i in idx)

    out = s
    for m in reversed(list(pattern.finditer(plain))):
        if m.start() == m.end():
            continue
        a, b = idx[m.start()], idx[m.end() - 1] + 1
        escapes = "".join(e.group() for e in ANSI_RE.finditer(out[a:b]))
        new = repl(m) if callable(repl) else m.expand(repl)
        out = out[:a] + new + escapes + out[b:]
    return out


def harvest_identifiers(texts: Iterable[str]) -> dict[str, str]:
    """Find usernames and hostnames in raw screens. Returns {literal: replacement}."""
    found: dict[str, str] = {}
    for text in texts:
        plain = strip_ansi(text) + "\n" + escape_payloads(text)
        for pat in _HOST_HARVEST:
            for m in pat.finditer(plain):
                found.setdefault(m.group(1), "host")
        for pat in _USER_HARVEST:
            for m in pat.finditer(plain):
                found.setdefault(m.group(1), "user")
    return {k: v for k, v in found.items() if len(k) >= 3 and k.lower() not in _NOT_SECRET}


def load_local_denylist(path: Path) -> dict[str, str]:
    """``.redact.local``: one literal per line, optionally ``literal<TAB>replacement``."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        literal, _, repl = line.partition("\t")
        out[literal] = repl or "<redacted>"
    return out


_DOMAIN_RE = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


def _literal_rules(words: dict[str, str]) -> list[tuple[str, re.Pattern[str], Repl]]:
    """Literal strings to scrub. A domain also scrubs all of its subdomains."""
    rules: list[tuple[str, re.Pattern[str], Repl]] = []
    # longest first so "box-a7" wins over a shorter overlapping literal
    for w, r in sorted(words.items(), key=lambda kv: -len(kv[0])):
        if _DOMAIN_RE.match(w):
            pat = rf"(?<![A-Za-z0-9.-])(?:[A-Za-z0-9-]+\.)*{re.escape(w)}(?![A-Za-z0-9-])"
            rules.append(("domain", re.compile(pat), "<domain>" if r == "<redacted>" else r))
        else:
            pat = rf"(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])"
            rules.append(("literal", re.compile(pat), r))
    return rules


def redact(text: str, extra: dict[str, str] | None = None) -> str:
    """Redact a screen (plain or ANSI-colored)."""
    rules = RULES + _literal_rules(extra or {})
    for _name, pat, repl in rules:
        text = sub_preserving_ansi(text, pat, repl)

    def scrub_osc(m: re.Match[str]) -> str:
        seq = m.group()
        if not seq.startswith("\x1b]"):
            return seq
        for _name, pat, repl in rules:
            seq = pat.sub(repl, seq)
        return seq

    return ANSI_RE.sub(scrub_osc, text)


def find_leaks(text: str, extra: dict[str, str] | None = None) -> list[str]:
    """Names of rules that still match after redaction (should be empty)."""
    plain = strip_ansi(text) + "\n" + escape_payloads(text)
    hits = []
    for name, pat, repl in RULES + _literal_rules(extra or {}):
        for m in pat.finditer(plain):
            replaced = repl(m) if callable(repl) else m.expand(repl)
            if m.group() != replaced:
                hits.append(name)
                break
    return hits


# ---------------------------------------------------------------- screens on disk

SCREEN_NAME_RE = re.compile(r"^(?P<staged>staged_)?(?:(?P<ts>\d{8}T\d{6}Z)_)?(?P<session>.+)$")


@dataclass(frozen=True)
class Screen:
    id: str  # file stem, e.g. 20261113T184324Z_books
    session: str
    ts: str  # capture time, UTC, as in the file name ("" if absent)
    source: str  # "live" or "staged"
    txt: Path
    ansi: Path | None

    def sha256(self) -> str:
        p = self.ansi or self.txt
        return hashlib.sha256(p.read_bytes()).hexdigest()

    def pane_command(self) -> str | None:
        """First line of the .txt: the pane's current command at capture time (claude/bash)."""
        first = self.txt.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0].strip()
        return first if PANE_CMD_RE.match(first) else None

    def screen(self) -> str:
        """Screen content with colors. The .ansi is authoritative; the .txt minus its
        first line is a fallback for captures without one."""
        if self.ansi:
            return self.ansi.read_text(encoding="utf-8", errors="replace")
        text = self.txt.read_text(encoding="utf-8", errors="replace")
        return text.split("\n", 1)[1] if self.pane_command() and "\n" in text else text


PANE_CMD_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,32}$")


def parse_stem(stem: str) -> tuple[str, str, str]:
    """Return (session, ts, source) for a capture file stem."""
    m = SCREEN_NAME_RE.match(stem)
    assert m  # the pattern matches any non-empty string
    source = "staged" if m.group("staged") else "live"
    return m.group("session"), m.group("ts") or "", source


def scan(directory: Path, settle_seconds: float = 3.0, now: float | None = None) -> list[Screen]:
    """List complete captures in a directory.

    A capture needs a ``.txt``; ``.ansi`` is optional. Files modified in the last
    ``settle_seconds`` are skipped because the capture job may still be writing them.
    """
    now = time.time() if now is None else now
    screens = []
    for txt in sorted(directory.glob("*.txt")):
        if now - txt.stat().st_mtime < settle_seconds:
            continue
        ansi = txt.with_suffix(".ansi")
        if ansi.exists() and now - ansi.stat().st_mtime < settle_seconds:
            continue
        session, ts, source = parse_stem(txt.stem)
        screens.append(Screen(txt.stem, session, ts, source, txt, ansi if ansi.exists() else None))
    return screens


def read_manifest(raw_dir: Path) -> dict[str, str]:
    """Optional ``staged_manifest.tsv``: ``stem<TAB>description`` of each staged capture."""
    path = raw_dir / "staged_manifest.tsv"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        stem, _, desc = line.partition("\t")
        if stem:
            out[stem.strip()] = desc.strip()
    return out


def sync(raw_dir: Path, out_dir: Path, local_denylist: Path) -> tuple[list[Screen], list[str]]:
    """Redact every capture in ``raw_dir`` into ``out_dir``.

    Returns the redacted screens and a list of ``"<id>: <rule>"`` for anything that
    still looks sensitive after redaction (should be empty).
    """
    raw = scan(raw_dir)
    raw_texts = []
    for s in raw:
        raw_texts.append(s.txt.read_text(encoding="utf-8", errors="replace"))
        if s.ansi:
            raw_texts.append(s.ansi.read_text(encoding="utf-8", errors="replace"))
    extra = harvest_identifiers(raw_texts) | load_local_denylist(local_denylist)

    out_dir.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    for s in raw:
        for src in (s.txt, s.ansi):
            if src is None:
                continue
            clean = redact(src.read_text(encoding="utf-8", errors="replace"), extra)
            problems += [f"{s.id}{src.suffix}: {r}" for r in find_leaks(clean, extra)]
            (out_dir / src.name).write_text(clean, encoding="utf-8")
    return scan(out_dir, settle_seconds=0), problems


def default_raw_dir() -> Path | None:
    v = os.environ.get("WNM_RAW_DIR")
    return Path(v).expanduser() if v else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--raw",
        type=Path,
        default=default_raw_dir(),
        help="directory of raw captures (default: $WNM_RAW_DIR)",
    )
    ap.add_argument("--out", type=Path, default=Path("data/screens"))
    ap.add_argument("--denylist", type=Path, default=Path(".redact.local"))
    args = ap.parse_args(argv)
    if args.raw is None:
        ap.error("set --raw or WNM_RAW_DIR")
    screens, problems = sync(args.raw, args.out, args.denylist)
    print(f"redacted {len(screens)} screens -> {args.out}")
    for p in problems:
        print(f"  possible leak: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
