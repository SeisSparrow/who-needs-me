"""Redaction tests. All samples are synthetic; no real screen data here."""

import os

import pytest

from who_needs_me.redact import (
    find_leaks,
    harvest_identifiers,
    parse_stem,
    redact,
    scan,
    strip_ansi,
    sync,
)

GREY = "\x1b[38;5;246m"
RESET = "\x1b[39m"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("server at 10.0.3.17:8080 is up", "server at <ip>:8080 is up"),
        ("curl 127.0.0.1:8598/health", "curl <ip>:8598/health"),
        ("mail alice.b@example.com now", "mail <email> now"),
        ("db.prod.internal refused", "<host> refused"),
        ("ssh ip-10-1-2-3 failed", "ssh <host> failed"),
        ("key sk-proj-abcdefghijklmnop1234 set", "key <token> set"),
        ("AIzaSyA1234567890abcdefghijklmnopqrstu", "<token>"),
        ("ghp_abcdefghijklmnopqrstuvwxyz0123", "<token>"),
        ("Authorization: Bearer abc.def-ghi_jkl", "Authorization: Bearer <token>"),
        ('{"api_key": "s3cr3tvalue"}', '{"api_key": "<token>"}'),
        ("GEMINI_API_KEY=abcdef123456", "GEMINI_API_KEY=<token>"),
        ("commit " + "a" * 40, "commit <hex>"),
        ("alice@box-a7:~/stage$ ls", "user@host:~/stage$ ls"),
        ("cd /home/alice/projects", "cd /home/user/projects"),
        ("/Users/alice/Desktop", "/Users/user/Desktop"),
        (
            "az storage blob list --storage-account stdemo42x -g rg-demo-west.v2 -o table",
            "az storage blob list --storage-account <storage-account> -g <resource-group> -o table",
        ),
        ("--account-name=stdemo42x", "--account-name=<storage-account>"),
        ("group rg-ml_jobs-01 created", "group <resource-group> created"),
        ("/tmp/claude-1000/-home-alice-projects-x/", "/tmp/claude-1000/-home-user-projects-x/"),
    ],
)
def test_rules(raw, expected):
    assert redact(raw) == expected


@pytest.mark.parametrize(
    "keep",
    [
        "✻ Thinking… (3m 37s · ↓ 10.4k tokens)",
        "esc to interrupt",
        "Ran 3 shell commands",
        "session 64c8c5bd-5e8b-465b-9dcf-db66f8decea4",
        "version 1.2.3",
        "npm i @types/node",
        "org-chart and the svg-rg-tool",
    ],
)
def test_ordinary_text_untouched(keep):
    assert redact(keep) == keep


def test_ansi_colors_preserved():
    raw = f"{GREY}ssh 10.0.0.5{RESET} done"
    out = redact(raw)
    assert out == f"{GREY}ssh <ip>{RESET} done"


def test_match_split_by_escape_code_still_redacted():
    raw = f"10.0.{GREY}0.5{RESET} up"
    out = redact(raw)
    assert strip_ansi(out) == "<ip> up"
    assert GREY in out and RESET in out  # color state kept


def test_osc8_hyperlink_url_redacted():
    link = "\x1b]8;id=ab;file:///home/alice/projects/x.py\x1b\\x.py\x1b]8;;\x1b\\"
    raw = f"see {link} on 10.0.0.9"
    assert find_leaks(raw)
    out = redact(raw)
    assert "alice" not in out and "10.0.0.9" not in out
    assert "file:///home/user/projects/x.py" in out
    assert find_leaks(out) == []


def test_harvest_reads_hyperlinks():
    raw = "\x1b]8;;file:///home/carol/a\x1b\\a\x1b]8;;\x1b\\"
    assert harvest_identifiers([raw]) == {"carol": "user"}


def test_ansi_and_txt_agree():
    raw = f"{GREY}alice@vm-x:~${RESET} cat /home/alice/a 10.1.1.1 bob@corp.example.com"
    assert strip_ansi(redact(raw)) == redact(strip_ansi(raw))


def test_harvest_then_scrub_truncated_username():
    raw = ["alice@box-a7:~$ claude", "  ⎿ /tmp/claude-1000/-home-alice-proj… mentioned alice"]
    extra = harvest_identifiers(raw)
    assert extra == {"box-a7": "host", "alice": "user"}
    out = redact("alice ran on box-a7, not malice", extra)
    assert out == "user ran on host, not malice"


def test_find_leaks_clean_after_redact():
    raw = "alice@vm-1x:~$ echo 10.0.0.1 a@b.io sk-ant-abcdefghijklmnopqrst"
    extra = harvest_identifiers([raw])
    assert find_leaks(raw, extra)
    assert find_leaks(redact(raw, extra), extra) == []


def test_denylisted_domain_scrubs_subdomains():
    extra = {"example.org": "<redacted>"}
    out = redact("see notes.example.org and https://example.org/x, not myexample.org", extra)
    assert out == "see <domain> and https://<domain>/x, not myexample.org"


def test_pane_command_and_screen(tmp_path):
    (tmp_path / "20261113T000000Z_a.txt").write_text("bash\n$ ls\n")
    (tmp_path / "20261113T000000Z_b.txt").write_text("claude\nplain\n")
    (tmp_path / "20261113T000000Z_b.ansi").write_text("\x1b[2mcolored\x1b[0m\n")
    a, b = scan(tmp_path, settle_seconds=0)
    assert (a.pane_command(), a.screen()) == ("bash", "$ ls\n")
    assert (b.pane_command(), b.screen()) == ("claude", "\x1b[2mcolored\x1b[0m\n")


def test_parse_stem():
    assert parse_stem("20261113T184324Z_books") == ("books", "20261113T184324Z", "live")
    assert parse_stem("staged_20261113T185707Z_stage") == ("stage", "20261113T185707Z", "staged")
    assert parse_stem("staged_foo") == ("foo", "", "staged")


def test_scan_skips_files_still_being_written(tmp_path):
    (tmp_path / "20261113T000000Z_a.txt").write_text("x")
    (tmp_path / "20261113T000000Z_a.ansi").write_text("x")
    (tmp_path / "20261113T000200Z_a.txt").write_text("y")
    old = 1_000_000.0
    for p in tmp_path.glob("*000000Z*"):
        os.utime(p, (old, old))
    os.utime(tmp_path / "20261113T000200Z_a.txt", (old + 100, old + 100))
    got = scan(tmp_path, settle_seconds=3, now=old + 101)
    assert [s.id for s in got] == ["20261113T000000Z_a"]
    assert got[0].ansi is not None


def test_sync_writes_redacted_copies(tmp_path):
    raw, out = tmp_path / "raw", tmp_path / "out"
    raw.mkdir()
    (raw / "staged_20261113T000000Z_s.txt").write_text("alice@box1:~$ ping 192.168.1.4\n")
    ansi = f"{GREY}alice@box1:~${RESET} ping 192.168.1.4\n"
    (raw / "staged_20261113T000000Z_s.ansi").write_text(ansi)
    for p in raw.iterdir():
        os.utime(p, (1, 1))
    screens, problems = sync(raw, out, tmp_path / "none")
    assert problems == []
    assert [s.source for s in screens] == ["staged"]
    text = (out / "staged_20261113T000000Z_s.txt").read_text()
    assert text == "user@host:~$ ping <ip>\n"
    assert "alice" not in (out / "staged_20261113T000000Z_s.ansi").read_text()
