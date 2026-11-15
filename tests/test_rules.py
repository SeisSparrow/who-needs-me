"""Layer-0 rules: one test per rule. Screens are synthetic, modeled on real Claude Code layout."""

from who_needs_me.rules import (
    content_key,
    evaluate,
    last_message,
    parse,
    split_dim,
    stale_minutes,
)

G = "\x1b[38;5;244m"  # border grey
R = "\x1b[39m"
DIM = "\x1b[2m"
OFF = "\x1b[0m"
BORDER = f"{G}{'─' * 60}"


def claude_screen(body: list[str], box: str = "❯\xa0", status: str = "  ⏵⏵ auto mode on") -> str:
    return "\n".join([*body, "", BORDER, f"{R}{box}", BORDER, f"{R}{status}", ""])


def rules_of(layer0):
    return {h.rule: h for h in layer0.hits}


IDLE_DONE = claude_screen(
    [
        "● Bash(pytest -q)",
        "  ⎿  40 passed",
        "",
        "● All 40 tests pass. The parser now handles empty files.",
        "  Nothing else is pending.",
        "",
        "✻ Baked for 3s · done 7:02 PM",
        "                         ✔ Update installed · /restart to apply",
    ]
)


def test_idle_screen_has_no_decisive_rule():
    l0 = evaluate(IDLE_DONE, "claude")
    assert l0.verdict() is None
    assert l0.facts["input_box"] is True
    assert l0.last_message == (
        "All 40 tests pass. The parser now handles empty files.\nNothing else is pending."
    )
    assert l0.evidence_default == "Nothing else is pending."


# ---- rule: dropped_to_shell


def test_dropped_to_shell_from_pane_command():
    lines = ["user@host:~/x$ 请继续上一步然后汇报", "请继续上一步然后汇报: command not found"]
    screen = "\n".join([*lines, "user@host:~/x$"])
    v = evaluate(screen, "bash").verdict()
    assert (v.rule, v.state, v.evidence) == (
        "dropped_to_shell",
        "dropped_to_shell",
        "pane_command=bash",
    )
    # the receipt wins over a screen that still shows a Claude Code box
    assert evaluate(IDLE_DONE, "bash").verdict().rule == "dropped_to_shell"
    assert evaluate(IDLE_DONE, "claude").verdict() is None


def test_dropped_to_shell_fallback_without_pane_command():
    screen = f"{DIM}Resume this session with:{OFF}\n\x1b[1muser@host\x1b[0m:~/x$ \n"
    assert evaluate(screen, None).verdict().rule == "dropped_to_shell"
    assert evaluate(IDLE_DONE, None).verdict() is None


# ---- rule: busy


def test_busy_spinner():
    screen = claude_screen(["● Bash(make test)", "", "✶ Tinkering… (1m 42s · ↓ 9.2k tokens)"])
    v = evaluate(screen, "claude").verdict()
    assert (v.rule, v.state) == ("busy", "busy")
    assert "Tinkering" in v.evidence


def test_busy_esc_to_interrupt_in_status():
    screen = claude_screen(["● Working"], status="  ⏵⏵ auto mode on · esc to interrupt")
    assert evaluate(screen, "claude").verdict().rule == "busy"


def test_done_line_is_not_busy():
    assert "busy" not in rules_of(evaluate(IDLE_DONE, "claude"))


# ---- rule: ghost text (dim suggestion in the input box)


def test_ghost_text_stripped_and_flagged():
    screen = claude_screen(["● Done."], box=f"❯\xa0{DIM}go ahead and start the T4 VM{OFF}")
    l0 = evaluate(screen, "claude")
    assert rules_of(l0)["ghost_text"].evidence == "go ahead and start the T4 VM"
    assert l0.facts["ghost_text_ignored"] is True
    assert l0.facts["user_typing"] is False
    assert l0.verdict() is None  # ghost text never decides anything, nor trips the tripwire


def test_typed_text_is_not_ghost():
    screen = claude_screen(["● Done."], box="❯\xa0go ahead and start the T4 VM")
    l0 = evaluate(screen, "claude")
    assert "ghost_text" not in rules_of(l0)
    assert l0.facts["user_typing"] is True


def test_split_dim_handles_256_color_params():
    assert split_dim(f"a\x1b[38;5;2mb{DIM}c\x1b[22md") == ("abd", "c")


# ---- rule: auth / API error (session broke)


def test_auth_error():
    screen = claude_screen(
        [
            "● Background command finished (exit code 0)",
            "",
            "● Please run /login · API Error: 403 Attention Required! | Cloudflare",
            "",
            "✻ Cogitated for 0s · done 9:50 AM",
        ]
    )
    l0 = evaluate(screen, "claude")
    v = l0.verdict()
    assert (v.rule, v.state, v.reason) == ("auth_error", "needs_human", "machine")
    assert v.evidence.startswith("● Please run /login")
    assert "API Error" in l0.facts["last_error_line"]


def test_old_auth_error_scrolled_away_does_not_fire():
    body = (
        ["● API Error: 500 Internal"] + [f"  line {i}" for i in range(20)] + ["● Fixed, all good."]
    )
    assert "auth_error" not in rules_of(evaluate(claude_screen(body), "claude"))


# ---- rule: trust prompt


def test_trust_prompt():
    screen = "\n".join(
        [
            "user@host:~/stage_tmp$ claude --model haiku",
            "─" * 60,
            " Accessing workspace:",
            " Quick safety check: Is this a project you created or one you",
            " trust? (Like your own code, a well-known open source project,",
            " ❯ No, exit",
            "   Yes, I trust this folder",
            " Enter to confirm · Esc to cancel",
        ]
    )
    v = evaluate(screen, "claude").verdict()
    assert (v.rule, v.state, v.reason) == ("trust_prompt", "needs_human", "machine")


# ---- rule: tripwire keywords (last message only)


def test_tripwire_per_reason():
    cases = {
        "deploy": "Ready to deploy v2 to production. Approve?",
        "spend": "This needs a $40/month GPU instance. OK to provision?",
        "delete": "I will rm -rf the old build cache once you confirm.",
        "machine": "Please log in on the VM so I can continue.",
    }
    for reason, text in cases.items():
        v = evaluate(claude_screen([f"● {text}"]), "claude").verdict()
        assert (v.rule, v.reason) == ("tripwire", reason), text
        assert v.evidence == text


def test_tripwire_chinese():
    v = evaluate(claude_screen(["● 代码已就绪，等你批准后上线。"]), "claude").verdict()
    assert (v.rule, v.reason) == ("tripwire", "deploy")


def test_tripwire_ignores_older_messages():
    body = ["● I will deploy after review.", "", "> thanks", "", "● Tests pass. Nothing pending."]
    assert "tripwire" not in rules_of(evaluate(claude_screen(body), "claude"))


def test_tripwire_only_scans_tail_latest_line_wins():
    narration = [f"  detail {i}" for i in range(6)]
    body = ["● Before each deploy the suite passed.", *narration, "  Done, nothing pending."]
    assert "tripwire" not in rules_of(evaluate(claude_screen(body), "claude"))
    body = ["● Old branches can be deleted later.", "  Approve the deploy of v2?"]
    v = evaluate(claude_screen(body), "claude").verdict()
    assert (v.reason, v.evidence) == ("deploy", "Approve the deploy of v2?")


def test_busy_beats_tripwire():
    body = ["● Deploying now.", "", "✶ Forging… (5s · ↓ 1k tokens)"]
    assert evaluate(claude_screen(body), "claude").verdict().rule == "busy"


# ---- rule: empty session (added after the first evaluation)

WELCOME = [
    " ▐▛███▜▌   Claude Code v2.1.322",
    "▝▜█████▛▘  Haiku 5.5 · Claude Ultra",
    "  ▘▘ ▝▝    ~/stage_tmp",
]


def test_empty_session_is_idle_without_model():
    from who_needs_me.policy import needs_model

    l0 = evaluate(claude_screen(WELCOME), "claude")
    v = l0.verdict()
    assert (v.rule, v.state) == ("empty_session", "idle_done")
    assert not needs_model(l0)
    # typed text in the input box does not change it
    typed = evaluate(claude_screen(WELCOME, box="❯\xa0please deploy"), "claude")
    assert typed.verdict().rule == "empty_session"
    # once there is any conversation, the rule no longer applies
    talked = evaluate(claude_screen([*WELCOME, "", "> hi", "", "● Hello."]), "claude")
    assert "empty_session" not in rules_of(talked)


# ---- rule: stale + background task


def test_stale_background():
    status = "  ⏵⏵ auto mode on · 1 shell · ↓ to manage"
    screen = claude_screen(["● Waiting for the re-embed job to finish."], status=status)
    l0 = evaluate(screen, "claude", stale=45)
    hit = rules_of(l0)["stale_background"]
    assert hit.state is None and "45 min" in hit.evidence
    assert l0.facts["background_task"] == "running"
    assert l0.facts["screen_unchanged"] == "over_30_min"
    assert "stale_background" not in rules_of(evaluate(screen, "claude", stale=10))
    no_bg = claude_screen(["● Waiting for the re-embed job to finish."])
    assert "stale_background" not in rules_of(evaluate(no_bg, "claude", stale=45))


def test_stale_minutes_ignores_spinner_timer():
    a = claude_screen(["● Same text", "✶ Tinkering… (1m 2s · ↓ 1k tokens)"])
    b = claude_screen(["● Same text", "✶ Tinkering… (3m 9s · ↓ 2k tokens)"])
    c = claude_screen(["● New text"])
    assert content_key(a) == content_key(b) != content_key(c)
    series = [
        ("20261113T180000Z", content_key(c)),
        ("20261113T181000Z", content_key(a)),
        ("20261113T184000Z", content_key(b)),
    ]
    assert stale_minutes(series) == 30


# ---- last message extraction


def test_last_message_stops_at_next_bullet_and_skips_tool_calls():
    body = parse(
        claude_screen(
            [
                "> please check",
                "● First I'll look.",
                "● Read(src/x.py)",
                "  ⎿  Read 10 lines",
                "● Found it. Shall I push the fix?",
                "  It is one line.",
            ]
        )
    ).body
    assert last_message(body) == "Found it. Shall I push the fix?\nIt is one line."


def test_last_message_fallback_and_cap():
    body = [f"line {i}" for i in range(100)]
    assert last_message(body, max_lines=3) == "line 97\nline 98\nline 99"


def test_rules_can_be_switched_off():
    from who_needs_me.rules import load_config

    cfg = load_config()
    off = {**cfg, "rules": {**cfg["rules"], "dropped_to_shell": False}}
    assert evaluate(IDLE_DONE, "bash", config=off).verdict() is None


# ---- who spoke last: a request that has been answered is not open any more

ASK = [
    "● The patch is ready on the release branch.",
    "  Going live needs one restart, and I will deploy once you approve.",
    "",
    "✻ Brewed for 12s · done 8:20 AM",
]


def test_request_then_a_persons_reply_is_busy_not_a_request():
    from who_needs_me.rules import evaluate

    waiting = evaluate(claude_screen(ASK))
    assert waiting.facts["who_spoke_last"] == "assistant"
    assert "once you approve" in waiting.last_message

    answered = evaluate(claude_screen([*ASK, "", "❯ I approve. Deploy it, then report the SHA."]))
    assert answered.facts["who_spoke_last"] == "user"
    assert answered.last_message == ""  # the request is not read again as if still open
    assert answered.verdict().rule == "prompt_submitted" and answered.verdict().state == "busy"
    assert "tripwire" not in rules_of(answered)
    assert answered.facts["last_prompt_key"] and not waiting.facts["last_prompt_key"]


def test_a_long_prompt_whose_first_line_scrolled_off_is_not_the_assistants_message():
    from who_needs_me.rules import evaluate

    tail_of_prompt = [
        "  the plan is written; report it to me and I will pass it to the user.",
        "  Redeploying the fix: I am asking the user and waiting for his answer.",
    ]
    l0 = evaluate(claude_screen(tail_of_prompt))
    assert l0.facts["who_spoke_last"] == "user" and l0.last_message == ""
    assert l0.verdict().state == "busy"
    # the same text under a finished-turn line is the assistant's own long message
    own = evaluate(claude_screen([*tail_of_prompt, "", "✻ Cooked for 2m · done 8:24 AM"]))
    assert (
        own.facts["who_spoke_last"] == "assistant" and "waiting for his answer" in own.last_message
    )


def test_who_spoke_last_edge_cases():
    from who_needs_me.rules import who_spoke_last

    assert who_spoke_last(["● Done.", "✻ Baked for 3s · done 7:02 PM"])[0] == "assistant"
    assert who_spoke_last(["● Done.", "› Message from @infra: your turn"])[0] == "peer"
    assert who_spoke_last(["● Done.", "❯ /clear"])[0] == "none"  # a slash command is not a prompt
    # a markdown quote inside a message is indented, so it is not a prompt
    who, prompt = who_spoke_last(["● You wrote:", "  > deploy it", "  I will, once you approve."])
    assert (who, prompt) == ("assistant", "")
    # the newest prompt is reported even when the assistant has answered since
    who, prompt = who_spoke_last(["❯ first", "● ok", "❯ second line one", "  line two", "● done"])
    assert who == "assistant" and prompt == "second line one\nline two"


def test_content_key_survives_a_resize_but_not_new_content():
    from who_needs_me.rules import KEY_TAIL_CHARS, content_key

    words = ("The migration is finished and verified against the old tables. " * 12).split()
    assert len("".join(words)) > KEY_TAIL_CHARS  # the tail is all shared text

    def wrapped(width: int) -> list[str]:
        lines, cur = [], ""
        for w in words:
            if len(cur) + len(w) + 1 > width:
                lines.append("  " + cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        return [*lines, "  " + cur]

    table = ["  ┌─────────┬────────┐", "  │ Version │ Median │", "  └─────────┴────────┘"]
    end = ["  I will deploy once you approve.", "✻ Brewed for 12s · done 8:20 AM"]
    wide = claude_screen(
        ["● Bash(pytest -q)", "  ⎿  40 passed", "● Report.", *wrapped(96), *table, *end]
    )
    # the same conversation in a narrower pane: re-wrapped, with less history above it
    narrow = claude_screen([*wrapped(40)[3:], *table, *end])
    assert content_key(wide) == content_key(narrow)
    ticking = wide.replace("Brewed for 12s", "Brewed for 13s")  # a timer is not content
    assert content_key(ticking) == content_key(wide)
    answered = claude_screen([*wrapped(40)[3:], *table, *end, "", "❯ Approved."])
    assert content_key(answered) != content_key(narrow)


def test_a_wrapped_finished_turn_line_is_not_content():
    from who_needs_me.rules import content_key, evaluate

    msg = ["● The regression is running in the background.", "  It should end around 09:40."]
    wide = claude_screen([*msg, "✻ Cogitated for 3m 43s · done 9:09 AM · 2 shells still running"])
    # the same screen in a pane too narrow for the status line
    narrow = claude_screen(
        [*msg, "✻ Cogitated for 3m 43s · done 9:09 AM · 2 shells still", "  running"]
    )
    assert content_key(wide) == content_key(narrow)
    assert evaluate(narrow).last_message == evaluate(wide).last_message
    assert evaluate(narrow).facts["who_spoke_last"] == "assistant"
    # a real new message after the status line still counts
    more = claude_screen([*msg, "✻ Cogitated for 3m 43s · done 9:09 AM", "● One more thing."])
    assert content_key(more) != content_key(wide)


def test_a_very_narrow_pane_reads_the_same_as_a_wide_one():
    """25 columns: the finished-turn line breaks before "done" and the footer notice is cut."""
    from who_needs_me.rules import content_key, evaluate

    wide = claude_screen(
        [
            "● says check_store_ack takes about 2 min, but it took 4.7 min.",
            "",
            "✻ Worked for 6m 28s · done 9:06 AM · 1 shell still running",
            "                         ✔ Update installed · /restart to apply",
        ]
    )
    narrow = claude_screen(
        [
            "● says check_store_ack",
            "  takes about 2 min, but",
            "  it took 4.7 min.",
            "",
            "✻ Worked for 6m 28s ·",
            "  done 9:06 AM · 1 shell",
            "  still running",
            "   ✔ Update installed …",
        ]
    )
    cut = claude_screen([*narrow.split("\n")[:7], "  new tas… · ✔ Update …"])
    assert content_key(narrow) == content_key(wide) == content_key(cut)
    for screen in (narrow, cut):
        l0 = evaluate(screen)
        assert l0.facts["who_spoke_last"] == "assistant"
        assert l0.last_message.replace("\n", " ") == evaluate(wide).last_message
        assert "running" not in l0.last_message and "Update" not in l0.last_message


def test_keyword_tail_follows_the_pane_width():
    """The keyword scan covers the last 4 lines of a wide pane. A narrow pane wraps those
    sentences into more lines, and must still flag the same request."""
    from who_needs_me.rules import evaluate

    wide = claude_screen(
        [
            "● The fix is ready and tested. I will deploy it to production once you approve.",
            "  The diff is saved in the plan folder.",
            "  Nothing else changed.",
            "  Tests: 40 passed.",
        ]
    )
    narrow = claude_screen(
        [
            "● The fix is ready and",
            "  tested. I will deploy",
            "  it to production once",
            "  you approve.",
            "  The diff is saved in",
            "  the plan folder.",
            "  Nothing else changed.",
            "  Tests: 40 passed.",
        ]
    )
    assert "tripwire" in rules_of(evaluate(wide))
    assert "tripwire" not in rules_of(evaluate(narrow))  # width unknown: 4 lines, as before
    assert rules_of(evaluate(narrow, meta={"width": 25}))["tripwire"].reason == "deploy"


def test_notices_from_the_tool_are_not_the_sessions_message():
    """After a while Claude Code compacts an idle conversation and says so with a bullet,
    below the finished turn. The session's last message is still the one above."""
    from who_needs_me.rules import content_key, evaluate

    msg = ["● The dry run is at 10:33.", "  I will post its result before starting the VM."]
    done = "✻ Crunched for 21s · done 9:20 AM · 2 shells still running"
    before = claude_screen([*msg, "", done])
    after = claude_screen(
        [
            *msg,
            "",
            done,
            "  ⎿  Referenced file",
            "     ../_docs/_plan/notes.md",
            '● Task "Wait until 10:30 UTC for session A pre-checks" still',
            "  running in background",
            "● Compacted while idle, before the prompt cache expired",
            '  · turn off with "idleCompaction": false in',
            "  settings.json",
        ]
    )
    assert content_key(after) == content_key(before)
    assert evaluate(after).last_message == evaluate(before).last_message
    assert evaluate(after).facts["who_spoke_last"] == "assistant"


def test_only_notices_on_screen_is_said_plainly():
    """Compacted while idle, then the pane was resized: the conversation is gone from the
    screen and only the tool's notices remain. The file list must not be read as a message."""
    from who_needs_me.policy import decide
    from who_needs_me.rules import evaluate

    screen = claude_screen(
        [
            "  ⎿  Referenced file ../_infra-image/gpu/tests/test_image_t1.sh",
            "  ⎿  Referenced file ../_docs/_plan/notes_infra_makeup_",
            "     sessions_2026-11-15.md",
            '● Task "Wait until 10:30 UTC for session A pre-checks" still running in background',
            "● Compacted while idle, before the prompt cache",
            '  expired · turn off with "idleCompaction":',
            "  false in settings.json",
        ]
    )
    l0 = evaluate(screen, "claude")
    assert l0.last_message == ""
    d = decide(l0, None)
    assert (d.state, d.human_reason, d.tier, d.decided_by) == (
        "needs_human",
        "low_confidence",
        "yellow",
        "rule",
    )
    assert "compacted" in d.evidence
