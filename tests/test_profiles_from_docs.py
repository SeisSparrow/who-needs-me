"""Profiles that could not be checked on a real screen (``verified: source`` or ``partly``).

These screens are assembled by hand from strings in each tool's source code or
documentation. They show that the profile matches what the tool is documented to print;
they do not show what the tool really prints. A real capture belongs in
``tests/fixtures`` and replaces the case here.
"""

import pytest

from who_needs_me.policy import decide, needs_model
from who_needs_me.profiles import load_profiles
from who_needs_me.rules import evaluate


def read(command, *lines, **meta):
    l0 = evaluate("\n".join(lines) + "\n", command, 0, meta=meta or None)
    if needs_model(l0):
        return "MODEL", l0
    d = decide(l0, None)
    return (f"needs_human:{d.human_reason}" if d.state == "needs_human" else d.state), l0


CODEX_FOOT = ["› Ask Codex to do anything", "  ? for shortcuts            100% context left"]
QWEN_HEAD = [">_ Qwen Code", "Qwen OAuth | qwen3-coder (/model to change)", ""]
QWEN_BOX = ["─" * 60, " >   Type your message or @path/to/file", "─" * 60]

CASES = {
    # ---- Codex 0.169 (codex-rs/tui snapshots)
    "codex busy": (
        "busy",
        "codex",
        ["› fix the failing test", "", "• Working (1m 05s • esc to interrupt)", *CODEX_FOOT],
    ),
    "codex asks to run a command": (
        "needs_human:approval",
        "codex",
        [
            "› fix the failing test",
            "",
            "  Would you like to run the following command?",
            "",
            "  Reason: run the test suite",
            "  $ pytest -q",
            "",
            "› 1. Yes, proceed (y)",
            "  2. Yes, and don't ask again for commands that start with `pytest` (p)",
            "  3. No, and tell Codex what to do differently (esc)",
            "",
            "  Press enter to confirm or esc to cancel",
        ],
    ),
    "codex asks to edit": (
        "needs_human:approval",
        "codex",
        [
            "› rename the function",
            "  Would you like to make the following edits?",
            "› 1. Yes, proceed (y)",
            "  2. Yes, and don't ask again for these files (a)",
        ],
    ),
    "codex turn finished": (
        "MODEL",
        "codex",
        [
            "› say hi",
            "",
            "• Hello! The tests pass.",
            "",
            "  Worked for 3s • 3:42 PM",
            "",
            *CODEX_FOOT,
        ],
    ),
    "codex usage limit": (
        "needs_human:machine",
        "codex",
        [
            "› go on",
            "■ You’ve hit your usage limit. Upgrade to Pro or try again later.",
            *CODEX_FOOT,
        ],
    ),
    "codex under an old node": (  # the launcher does not replace itself: tmux says "node"
        "busy",
        "node",
        ["  >_ OpenAI Codex (v0.169.0)", "› hi", "◦ Working (2s • esc to interrupt)", *CODEX_FOOT],
    ),
    # ---- Qwen Code 0.27 (packages/cli/src/ui)
    "qwen busy": (
        "busy",
        "node",
        [
            *QWEN_HEAD,
            "> fix it",
            "",
            "  ⠋ I'm Feeling Lucky (12s · ↓ 1.2k tokens · esc to cancel)",
            *QWEN_BOX,
        ],
    ),
    "qwen asks to run a command": (
        "needs_human:approval",
        "node",
        [
            *QWEN_HEAD,
            "> run the tests",
            "◆ I will run the test suite.",
            "╭──────────────────────────────╮",
            "│ Allow execution of: 'npm test'? │",
            "│ › 1. Yes, allow once            │",
            "│   2. Always allow in this project │",
            "│   3. No, suggest changes (esc)  │",
            "╰──────────────────────────────╯",
            "  ⠏ Waiting for user confirmation...",
        ],
    ),
    "qwen finished": (
        "MODEL",
        "node",
        [
            *QWEN_HEAD,
            "> say hi",
            "◆ Hello. Nothing else to do.",
            *QWEN_BOX,
            "? for shortcuts   0.1% context used",
        ],
    ),
    "qwen login expired": (
        "needs_human:machine",
        "node",
        [
            *QWEN_HEAD,
            "> go on",
            "✕ Qwen OAuth credentials expired. Please use /auth to re-authenticate.",
            *QWEN_BOX,
        ],
    ),
    # ---- opencode 1.19 (packages/tui/src)
    "opencode busy": (
        "busy",
        "opencode",
        [
            "┃  fix the bug",
            "",
            "   Looking at the parser.",
            "",
            "▣  Build · gpt-5",
            "",
            "■■■⬝⬝⬝⬝⬝  esc interrupt",
        ],
    ),
    "opencode asks permission": (
        "needs_human:approval",
        "opencode",
        [
            "┃  clean the build folder",
            "",
            "△ Permission required",
            "# Shell command",
            "$ rm -rf build",
            "",
            "Allow once   Allow always   Reject            ⇆ select  enter confirm",
        ],
    ),
    "opencode finished": (
        "MODEL",
        "opencode",
        [
            "┃  fix the bug",
            "",
            "   Fixed: the parser now handles empty files.",
            "",
            "▣  Build · gpt-5 · 12.3s",
            "",
            "~/project                              tab agents  ctrl+p commands",
        ],
    ),
    # ---- Grok Build 1.0 (crates/codegen/xai-grok-pager)
    "grok busy": (
        "busy",
        "grok",
        ["fix the bug", "", "⠹ Thinking…                 12s ⇣1.2k [stop]", "Build anything"],
    ),
    "grok asks permission": (
        "needs_human:approval",
        "grok",
        [
            "clean up",
            "",
            "Allow `rm`?",
            "1 (●) Yes, proceed",
            "2 (○) Yes, and don't ask again for bash commands",
            "3 (○) No, reject (type to add feedback)",
        ],
    ),
    "grok finished": (
        "MODEL",
        "grok",
        ["The tests pass now.", "", "Worked for 2m5s", "", "Build anything"],
    ),
    # ---- Cursor CLI 2026.11 (bundle strings)
    "cursor busy": (
        "busy",
        "cursor-agent",
        ["Reading src/main.ts", "", "→ Add a follow-up                 ctrl+c to stop"],
    ),
    "cursor asks to run a command": (
        "needs_human:approval",
        "cursor-agent",
        [
            "  $ rm -rf build",
            "  Run this command?",
            "  Run (once) (y)",
            "  Add Shell(rm) to allowlist? (tab)",
            "  Skip & tell the agent what to do instead (esc or n)",
        ],
    ),
    "cursor not logged in": (
        "needs_human:machine",
        "cursor-agent",
        ["  Press any key to log in..."],
    ),
    # ---- Kiro CLI (classic strings from the Q CLI source; V2/V3 from third-party notes)
    "kiro classic asks": (
        "needs_human:approval",
        "kiro-cli",
        [
            "🛠️  Using tool: execute_bash",
            " ● I will run the following shell command: rm -rf build",
            "",
            "Allow this action? Use 't' to trust (always allow) this tool for the session. [y/n/t]:",  # noqa: E501
        ],
    ),
    "kiro v3 asks": (
        "needs_human:approval",
        "kiro-cli",
        [
            "• Shell rm -rf build",
            "Shell requires approval",
            "❯ Allow once",
            "  Deny once",
            "  Create deny rule…",
            "esc to close · ↑↓ to navigate · ↵ to select",
        ],
    ),
    "kiro busy": (
        "busy",
        "kiro-cli",
        [
            "  fix the bug",
            "",
            "Thinking... (esc to cancel)",
            "─" * 40,
            "Ask a question or describe a task ↵",
        ],
    ),
    "kiro finished": (
        "MODEL",
        "kiro-cli",
        [
            "• The bug is fixed.",
            "▸ Credits: 0.20 • Time: 29s",
            "─" * 40,
            "Ask a question or describe a task ↵",
        ],
    ),
    # ---- Trae Agent (trae_agent/utils/cli/simple_console.py): no spinner, no approval prompts
    "trae running": (
        "busy",
        "python3.12",
        [
            "Task: fix the bug",
            "Working Directory: /repo",
            "Executing task: fix the bug",
            "✅ Step 1: Completed",
        ],
    ),
    "trae finished": (
        "MODEL",
        "python3.12",
        [
            "Executing task: fix the bug",
            "✅ Step 3: Completed",
            "Execution Summary",
            "Success        ✅ Yes",
            "Trajectory saved to: trajectory_1.json",
            "Task: ",
        ],
    ),
}


@pytest.mark.parametrize("name", CASES)
def test_documented_screen(name):
    want, command, lines = CASES[name]
    got, l0 = read(command, *lines)
    assert got == want, (l0.facts["tool"], l0.hits)
    assert l0.facts["tool"] == name.split()[0]


def test_terminal_title_is_a_receipt():
    """Codex, Gemini CLI, Qwen Code and Grok Build put "Action Required" in the terminal
    title while they wait for an approval. The title is current even when the dialog itself
    is not recognised on screen."""
    screen = ["› fix it", "• A panel this profile does not know", *CODEX_FOOT]
    assert read("codex", *screen)[0] == "MODEL"
    got, l0 = read("codex", *screen, title="[ ! ] Action Required | fix it")
    assert got == "needs_human:approval" and l0.verdict().rule == "title_approval"
    got, l0 = read("grok", "do it", "", title="⚠ Action Required - Running: rm - grok")
    assert got == "needs_human:approval"
    # opencode is recognised by its title even under a generic process name
    assert (
        read("node", "┃  hi", "■⬝⬝⬝  esc interrupt", title="OC | fix the bug")[1].facts["tool"]
        == "opencode"
    )


def test_every_profile_says_how_far_it_was_checked():
    for name, p in load_profiles().items():
        assert p.verified in ("real", "partly", "source"), name
        assert p.markers, name
