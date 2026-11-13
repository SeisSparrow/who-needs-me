import json
import subprocess
from datetime import UTC, datetime

import pytest

from tests.test_policy_decide_split import answers
from tests.test_rules import IDLE_DONE, claude_screen
from who_needs_me.board import Board
from who_needs_me.capture import ForbiddenTmuxCommand, capture_once, tmux

# ---- capture: read-only


class FakeTmux:
    def __init__(self, screens: dict[str, str]):
        self.screens = screens
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        argv = cmd if cmd[0] == "tmux" else cmd[2].split()
        if argv[1] == "list-panes":
            out = "".join(f"{s}\t{s}:0.0\tclaude\t11\n" for s in self.screens)
        else:
            out = self.screens[argv[argv.index("-t") + 1].split(":")[0]]
        return subprocess.CompletedProcess(cmd, 0, out, "")


@pytest.mark.parametrize("sub", ["send-keys", "send", "paste-buffer", "kill-session", "run"])
def test_tmux_refuses_anything_but_reading(sub):
    fake = FakeTmux({})
    with pytest.raises(ForbiddenTmuxCommand):
        tmux([sub, "-t", "x", "hello", "Enter"], runner=fake)
    assert fake.calls == []  # refused before any process starts


def test_capture_once_writes_classifier_format(tmp_path):
    fake = FakeTmux({"books": "\x1b[2mghost\x1b[0m\n● Done.\n"})
    now = datetime(2026, 11, 13, 20, 0, 0, tzinfo=UTC)
    files = capture_once(tmp_path, runner=fake, now=now)
    assert [f.name for f in files] == ["20261113T200000Z_books.txt"]
    assert (tmp_path / "20261113T200000Z_books.txt").read_text() == "claude\nghost\n● Done.\n"
    assert "\x1b[2m" in (tmp_path / "20261113T200000Z_books.ansi").read_text()
    used = {c[1] for c in fake.calls}
    assert used == {"list-panes", "capture-pane"}


def test_capture_over_ssh_only_reads(tmp_path):
    fake = FakeTmux({"a": "x\n"})
    capture_once(tmp_path, ssh="vm", runner=fake)
    assert all(c[:2] == ["ssh", "vm"] for c in fake.calls)
    assert not any("send-keys" in " ".join(c) for c in fake.calls)


# ---- board: sticky needs_human, dismiss, redaction before the model


def write(dir_, ts, session, screen, cmd="claude"):
    (dir_ / f"{ts}_{session}.ansi").write_text(screen)
    (dir_ / f"{ts}_{session}.txt").write_text(f"{cmd}\n{screen}")


def test_board_sticky_until_dismissed(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    cap = tmp_path / "cap"
    cap.mkdir()
    sent = []

    def fake(url, headers, body, timeout):
        sent.append(body["input"])
        wants = {"deploy": 0.95} if "deploy" in body["input"] else {}
        choice = "awaiting_approval" if wants else "reporting_done"
        return 200, {"answers": answers(choice, 0.9, **wants), "usage": {}}

    ask = claude_screen(["● Ready. I will deploy to production once you approve. ssh 10.1.2.3"])
    write(cap, "20261113T200000Z", "menu", ask)
    write(cap, "20261113T200000Z", "notes", IDLE_DONE)
    board = Board(
        cap, tmp_path / "state", transport=fake, settle_seconds=0, denylist=tmp_path / "none"
    )
    snap = board.snapshot()
    rows = {r["session"]: r for r in snap["sessions"]}
    assert snap["sessions"][0]["session"] == "menu"  # needs_human sorts first
    assert (rows["menu"]["state"], rows["menu"]["human_reason"]) == ("needs_human", "deploy")
    assert not any("10.1.2.3" in s for s in sent)  # redacted before the model

    # session moves on: still held on the board
    write(cap, "20261113T200200Z", "menu", IDLE_DONE)
    row = next(r for r in board.snapshot()["sessions"] if r["session"] == "menu")
    assert row["state"] == "needs_human" and row["current_state"] == "idle_done"
    assert row["sticky"]["since"] == "20261113T200000Z"

    # only a human clears it
    assert board.ack("menu")
    row = next(r for r in board.snapshot()["sessions"] if r["session"] == "menu")
    assert row["state"] == "idle_done" and row["sticky"] is None
    assert json.loads((tmp_path / "state" / "acks.jsonl").read_text())["session"] == "menu"


def test_board_rules_only_and_replay(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    write(cap, "20261113T200000Z", "a", IDLE_DONE)
    write(cap, "20261113T200200Z", "a", IDLE_DONE, cmd="bash")
    board = Board(
        cap, tmp_path / "s", use_model=False, settle_seconds=0, denylist=tmp_path / "none"
    )
    assert board.timeline() == ["20261113T200000Z", "20261113T200200Z"]
    assert board.snapshot()["sessions"][0]["state"] == "dropped_to_shell"
    past = board.snapshot(at="20261113T200000Z", sticky=False)
    assert past["sessions"][0]["state"] == "idle_done"
