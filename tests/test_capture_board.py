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


# ---- one board for live and history


def test_model_is_asked_once_per_distinct_message(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    cap = tmp_path / "cap"
    cap.mkdir()
    calls = []

    def fake(url, headers, body, timeout):
        calls.append(body["input"])
        return 200, {"answers": answers("awaiting_approval", 0.9, deploy=0.95), "usage": {}}

    ask = claude_screen(["● Ready. I will deploy to production once you approve."])
    board = Board(
        cap, tmp_path / "state", transport=fake, settle_seconds=0, denylist=tmp_path / "none"
    )
    # an idle session is captured again and again with the same screen
    for ts in ("20261113T200000Z", "20261113T200005Z", "20261113T200010Z"):
        write(cap, ts, "menu", ask)
        row = board.snapshot()["sessions"][0]
        assert (row["state"], row["human_reason"]) == ("needs_human", "deploy")
    assert len(calls) == 1


def test_history_is_available_next_to_live(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    board = Board(
        cap, tmp_path / "s", use_model=False, settle_seconds=0, denylist=tmp_path / "none"
    )
    write(cap, "20261113T200000Z", "a", IDLE_DONE)
    assert board.snapshot()["live"] is True
    write(cap, "20261113T200200Z", "a", IDLE_DONE, cmd="bash")
    write(cap, "20261113T200200Z", "b", IDLE_DONE)

    live = board.snapshot()
    assert {r["session"]: r["state"] for r in live["sessions"]} == {
        "a": "dropped_to_shell",
        "b": "idle_done",
    }
    past = board.snapshot(at="20261113T200100Z", sticky=False)  # between two captures
    assert past["live"] is False and past["as_of"] == "20261113T200100Z"
    assert [(r["session"], r["state"]) for r in past["sessions"]] == [("a", "idle_done")]
    assert board.snapshot(at="20261113T195900Z", sticky=False)["sessions"] == []  # before any
    assert not (tmp_path / "s" / "sticky.json").exists() or board.sticky == {}


def test_timeline_window_and_stale_minutes_from_the_index(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    for ts in ("20261113T120000Z", "20261113T195000Z", "20261113T200000Z"):
        write(cap, ts, "a", IDLE_DONE)
    board = Board(
        cap, tmp_path / "s", use_model=False, settle_seconds=0, denylist=tmp_path / "none"
    )
    assert len(board.timeline()) == 3
    assert board.timeline(hours=1) == ["20261113T195000Z", "20261113T200000Z"]
    # the same screen since 12:00: unchanged for 480 minutes, at 19:50 for 470
    assert board.snapshot()["sessions"][0]["facts"]["stale_minutes"] == 480
    past = board.snapshot(at="20261113T195000Z", sticky=False)
    assert past["sessions"][0]["facts"]["stale_minutes"] == 470
