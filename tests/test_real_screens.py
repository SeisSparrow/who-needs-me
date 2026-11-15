"""Real screens, captured on 2026-11-15 from Claude Code 2.1.325, Gemini CLI 0.68.0,
Codex 0.169.0 and plain terminal programs, with the user name and login tokens removed.

Each file is what tmux returned for a pane in that state; the name says what a person
looking at the pane would call it. ``MODEL`` means no rule may decide: the session has
finished saying something and the decision model has to read it.
"""

import json
from pathlib import Path

import pytest

from who_needs_me.policy import decide, needs_model
from who_needs_me.rules import evaluate

FIXTURES = Path(__file__).parent / "fixtures"

EXPECTED = {
    "claude": {
        "after_exit": "dropped_to_shell",
        "approval_bash": "needs_human:approval/red",
        "approval_bash_notified": "needs_human:approval/red",
        "busy_after_prompt": "busy",
        "busy_stop_hook": "busy",
        "busy_thinking": "busy",
        "done_after_tool": "MODEL",
        "empty_session": "idle_done",
        "not_logged_in": "needs_human:machine/red",
        "trust_folder": "needs_human:machine/red",
    },
    "gemini": {
        "after_quit": "dropped_to_shell",
        "approval_edit": "needs_human:approval/red",
        "approval_shell": "needs_human:approval/red",
        "auth_choose": "needs_human:machine/red",
        "auth_failed": "needs_human:machine/red",
        "auth_waiting": "needs_human:machine/red",
        "busy_after_tool": "busy",
        "busy_streaming": "busy",
        "busy_subject": "busy",
        "busy_thinking": "busy",
        "denied_edit": "MODEL",
        "done_after_tool": "MODEL",
        "empty_session": "idle_done",
        "trust_folder": "needs_human:machine/red",
        "typing_not_sent": "idle_done",
        # the same states with its hooks installed and its terminal title recorded
        "hooks_working": "busy",
        "hooks_approval": "needs_human:approval/red",
        "hooks_busy_after_approval": "busy",  # the hook note still says "approval"
        "hooks_done": "MODEL",
    },
    "codex": {
        "after_exit": "dropped_to_shell",
        "empty_session": "idle_done",
        "quota_exceeded": "needs_human:machine/red",
        "trust_folder": "needs_human:machine/red",
        "typing_not_sent": "idle_done",
        "hooks_busy": "busy",
        "hooks_quota_exceeded": "needs_human:machine/red",  # the hook note still says "working"
    },
    "terminal": {
        "editor_nano": "needs_human:input/yellow",
        "editor_vim": "needs_human:input/yellow",  # tmux reports "git", not "vim"
        "git_username": "needs_human:input/red",
        "idle_shell": "dropped_to_shell",
        "key_passphrase": "needs_human:input/red",
        "more_pager": "needs_human:input/yellow",
        "npx_install": "dropped_to_shell",  # the command failed and returned to the prompt
        "npx_real": "needs_human:input/red",
        "pager_gitlog": "dropped_to_shell",  # one commit: the pager exits by itself
        "pager_less": "needs_human:input/yellow",
        "pager_man": "needs_human:input/yellow",  # tmux reports "bash", not "less"
        "python_input": "needs_human:input/red",
        "read_yn": "needs_human:input/red",  # a script in a shell: not "dropped to shell"
        "running_loop": "busy",
        "script_yesno": "needs_human:input/red",
        "ssh_hostkey": "needs_human:input/red",
        "sudo_password": "needs_human:input/red",
    },
}
CASES = [(tool, name, want) for tool, table in EXPECTED.items() for name, want in table.items()]


def load(tool: str, name: str):
    base = FIXTURES / tool / name
    command = base.with_suffix(".txt").read_text(encoding="utf-8").split("\n", 1)[0]
    meta_file = base.with_suffix(".meta.json")
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else None
    return base.with_suffix(".ansi").read_text(encoding="utf-8"), command, meta


def reading(tool: str, name: str):
    screen, command, meta = load(tool, name)
    l0 = evaluate(screen, command, 0, meta=meta)
    if needs_model(l0):
        return "MODEL", l0
    d = decide(l0, None)
    if d.state == "needs_human":
        return f"needs_human:{d.human_reason}/{d.tier}", l0
    return d.state, l0


@pytest.mark.parametrize(("tool", "name", "want"), CASES, ids=[f"{t}/{n}" for t, n, _ in CASES])
def test_real_screen(tool, name, want):
    got, l0 = reading(tool, name)
    assert got == want, l0.hits


def test_every_fixture_has_an_expected_reading():
    on_disk = {(p.parent.name, p.stem) for p in FIXTURES.glob("*/*.txt")}
    assert on_disk == {(t, n) for t, n, _ in CASES}


@pytest.mark.parametrize(
    ("tool", "name", "detected"),
    [
        ("claude", "approval_bash", "claude"),  # process name is "2.1.325"
        ("gemini", "busy_thinking", "gemini"),  # process name is "node"
        ("codex", "empty_session", "codex"),
        ("terminal", "npx_real", "terminal"),  # also "node", but no agent on screen
        ("terminal", "read_yn", "shell"),
    ],
)
def test_the_tool_is_recognised(tool, name, detected):
    assert reading(tool, name)[1].facts["tool"] == detected


def test_evidence_is_the_question_on_screen():
    assert "touch /tmp/wnm_probe_file" in reading("claude", "approval_bash")[1].verdict().evidence
    assert reading("gemini", "approval_shell")[1].verdict().evidence.startswith("Allow execution")
    assert "Quota exceeded" in reading("codex", "quota_exceeded")[1].verdict().evidence
    assert "[Y/n]" in reading("terminal", "read_yn")[1].verdict().evidence
    assert reading("claude", "not_logged_in")[1].verdict().evidence.startswith("Not logged in")


def test_the_last_message_is_what_the_agent_said():
    msg = reading("gemini", "done_after_tool")[1].last_message
    assert msg.endswith("executed successfully, creating /tmp/wnm_probe_file.")
    assert "Type your message" not in msg and "workspace (/directory)" not in msg
    msg = reading("claude", "done_after_tool")[1].last_message
    assert msg.startswith("-rw-r--r--") and "Baked" not in msg


def test_a_hook_note_alone_raises_the_flag():
    """The dialog scrolled out of the captured lines, but the hook said "approval"."""
    screen, command, _ = load("claude", "done_after_tool")
    note = {"hook": "1794735792 claude approval PermissionRequest"}
    l0 = evaluate(screen, command, 0, meta=note)
    hit = l0.verdict()
    assert (hit.rule, hit.state, hit.reason) == ("hook_approval", "needs_human", "approval")
    # without the note the same screen is left to the model
    assert needs_model(evaluate(screen, command, 0))


def test_a_stale_working_note_does_not_hide_an_idle_session():
    """Hooks say nothing when a turn is interrupted. For a tool whose busy line was checked
    on real screens the note "working" is ignored; for the others it counts only while the
    screen still changes."""
    screen, command, _ = load("gemini", "done_after_tool")
    note = {"hook": "1794736422 gemini working BeforeAgent"}
    assert needs_model(evaluate(screen, command, 0, meta=note))
    screen, command, _ = load("codex", "typing_not_sent")
    note = {"hook": "1794736500 codex working UserPromptSubmit"}
    assert evaluate(screen, command, 0, meta=note).verdict().rule == "hook_working"
    assert evaluate(screen, command, 7, meta=note).verdict().rule == "empty_session"
