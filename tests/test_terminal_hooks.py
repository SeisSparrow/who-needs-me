"""The blocked-terminal check (things it must not flag), the hook script and its installer."""

import json
import subprocess

from tests.test_rules import claude_screen
from who_needs_me import hook, hooks
from who_needs_me.rules import evaluate, hook_note
from who_needs_me.terminal import blocked

# ---- terminal: what is not a question


def test_a_question_inside_an_agent_message_is_not_a_blocked_terminal():
    screen = claude_screen(["● I can overwrite the file. Do you want to continue? [y/N]"])
    assert "blocked_input" not in {h.rule for h in evaluate(screen, "claude").hits}


def test_only_the_line_the_cursor_waits_on_counts():
    lines = ["Do you want to continue? [Y/n] y", "Unpacking...", "Setting up libfoo (1.2) ..."]
    assert blocked(lines, "apt") is None
    assert blocked(["Proceed (y/n)? "], "bash").kind == "confirm"
    assert blocked(["dev@box:~$ echo '[y/n]'", "[y/n]", "dev@box:~$ "], "bash") is None


def test_programs_that_fill_the_screen_but_wait_for_nobody():
    assert blocked(["top - 10:00:01 up 3 days", "  PID USER  %CPU"], "top", alternate=True) is None
    assert blocked(["Every 2.0s: kubectl get pods"], "watch", alternate=True) is None


def test_secrets_and_confirmations_from_common_tools():
    for line, kind in [
        ("[sudo] password for dev: ", "secret"),
        ("dev@10.0.0.4's password: ", "secret"),
        ("Enter passphrase for key '/home/dev/.ssh/id_ed25519': ", "secret"),
        ("Password for 'https://dev@github.com': ", "secret"),
        ("Verification code: ", "secret"),
        ("Do you want to continue? [Y/n] ", "confirm"),
        ("Is this ok [y/N]: ", "confirm"),
        ("Proceed ([y]/n)? ", "confirm"),
        ("  Enter a value: ", "confirm"),
        ("Press any key to continue...", "confirm"),
        ("Stage this hunk [y,n,q,a,d,e,?]? ", None),  # not covered: git add -p
    ]:
        got = blocked(["output above", line], "bash")
        assert (got.kind if got else None) == kind, line


# ---- hook.py


def run_hook(argv, env, stdin=""):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    hook.read_payload.__globals__["sys"].stdin = _Stdin(stdin)
    try:
        assert hook.main(argv, env=env, run=fake_run) == 0
    finally:
        hook.read_payload.__globals__["sys"].stdin = __import__("sys").__stdin__
    return calls


class _Stdin:
    def __init__(self, text):
        self.text = text

    def isatty(self):
        return True  # read_payload then takes the payload from the argument only

    def read(self):
        return self.text


def note_of(calls):
    assert len(calls) == 1 and calls[0][:5] == ["tmux", "set-option", "-p", "-t", "%7"]
    return hook_note(calls[0][-1]) if calls[0][5] != "-u" else "cleared"


PANE = {"TMUX_PANE": "%7"}


def test_hook_events_become_notes_on_the_pane():
    for argv, kind in [
        (["claude", "UserPromptSubmit"], "working"),
        (["claude", "PermissionRequest"], "approval"),
        (["claude", "Notification", '{"notification_type": "permission_prompt"}'], "approval"),
        (
            ["claude", "Notification", '{"message": "Claude needs your permission to use Bash"}'],
            "approval",
        ),
        (["claude", "Notification", '{"notification_type": "idle_prompt"}'], "waiting"),
        (["claude", "Stop"], "done"),
        (["gemini", "Notification", '{"notification_type": "ToolPermission"}'], "approval"),
        (["gemini", "AfterAgent"], "done"),
        (["codex", "notify", '{"type": "agent-turn-complete"}'], "done"),
        (["cursor", "beforeSubmitPrompt"], "working"),
        (["kiro", "x", '{"hook_event_name": "agentSpawn"}'], "waiting"),
        (["opencode", "permission.asked", "{}"], "approval"),
        (["opencode", "session.status", '{"status": {"type": "busy"}}'], "working"),
        (["opencode", "session.status", '{"status": {"type": "idle"}}'], "done"),
        (["grok", "x", '{"hookEventName": "stop", "hook_event_name": "Stop"}'], "done"),
    ]:
        got = note_of(run_hook(argv, PANE))
        assert got["kind"] == kind and got["tool"] == argv[0], argv


def test_hook_does_nothing_it_should_not():
    assert run_hook(["claude", "Stop"], {}) == []  # not inside tmux
    assert run_hook(["claude", "Notification", '{"notification_type": "auth_success"}'], PANE) == []
    assert run_hook(["claude", "SomethingNew"], PANE) == []
    assert run_hook(["claude"], PANE) == []
    assert note_of(run_hook(["claude", "SessionEnd"], PANE)) == "cleared"


def test_hook_knows_who_really_called(capsys):
    """Grok Build and Cursor also run the hooks in Claude Code's settings file."""
    env = {**PANE, "GROK_HOOK_EVENT": "stop"}
    assert note_of(run_hook(["claude", "Stop"], env))["tool"] == "grok"
    assert (
        note_of(run_hook(["claude", "Stop"], {**PANE, "CURSOR_VERSION": "2026.11"}))["tool"]
        == "cursor"
    )
    assert capsys.readouterr().out == ""  # silent: some tools read a hook's output
    run_hook(["cursor", "--reply", '{"continue": true}', "beforeSubmitPrompt"], PANE)
    assert json.loads(capsys.readouterr().out) == {"continue": True}


def test_hook_survives_a_broken_tmux():
    def boom(cmd, **kw):
        raise OSError("no tmux")

    assert hook.main(["claude", "Stop"], env=PANE, run=boom) == 0


# ---- hooks.py: configuration


def test_installer_merges_without_touching_what_is_there(tmp_path):
    f = tmp_path / "settings.json"
    mine = {
        "model": "x",
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]},
    }
    f.write_text(json.dumps(mine))
    assert "updated" in hooks.write("claude", "/opt/hook.py", f, now=0)
    got = json.loads(f.read_text())
    assert got["model"] == "x"
    stop = [h["command"] for g in got["hooks"]["Stop"] for h in g["hooks"]]
    assert stop == ["say done", "python3 /opt/hook.py claude Stop"]
    assert "PermissionRequest" in got["hooks"]
    (backup,) = tmp_path.glob("settings.json.bak-*")
    assert json.loads(backup.read_text()) == mine
    assert "nothing changed" in hooks.write("claude", "/opt/hook.py", f, now=1)
    assert len(list(tmp_path.glob("settings.json.bak-*"))) == 1


def test_every_tool_has_a_configuration_or_a_reason():
    for tool in hooks.TOOLS:
        path, text = hooks.render(tool, "/opt/hook.py")
        assert "/opt/hook.py" in text and tool in text and path.startswith("~/")
        if not path.endswith(".js"):
            json.loads(text)
    assert set(hooks.TOOLS) | set(hooks.NO_HOOKS) == {
        "claude", "codex", "gemini", "qwen", "grok", "cursor", "kiro", "opencode", "trae"
    }  # fmt: skip
