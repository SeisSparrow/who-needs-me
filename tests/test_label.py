import json
from pathlib import Path

import pytest

from who_needs_me.label import KEYMAP, Labeler, make_row, order, read_labels, stats, suggestion
from who_needs_me.redact import Screen


def mk(tmp: Path, stem: str, content: str = "x", source: str = "live") -> Screen:
    session = stem.split("_")[-1]
    ts = stem.split("_")[-2] if "T" in stem else ""
    p = tmp / f"{stem}.txt"
    p.write_text(content)
    return Screen(stem, session, ts, source, p, None)


def test_keymap_reason_only_for_needs_human():
    for state, reason in KEYMAP.values():
        assert (state == "needs_human") == (reason != "none")


def test_make_row_rejects_inconsistent_label(tmp_path):
    s = mk(tmp_path, "20261113T000000Z_a")
    with pytest.raises(AssertionError):
        make_row(s, "h", "idle_done", "deploy")


def test_order_live_by_session_then_staged(tmp_path):
    a2 = mk(tmp_path, "20261113T000200Z_a")
    a1 = mk(tmp_path, "20261113T000000Z_a")
    b1 = mk(tmp_path, "20261113T000000Z_b")
    st = mk(tmp_path, "staged_20261113T000000Z_a", source="staged")
    assert [s.id for s in order([st, b1, a2, a1])] == [a1.id, a2.id, b1.id, st.id]


def test_order_only_staged(tmp_path):
    live = mk(tmp_path, "20261113T000000Z_a")
    st = mk(tmp_path, "staged_20261113T000000Z_a", source="staged")
    assert [s.id for s in order([live, st], only_staged=True)] == [st.id]


def test_label_undo_and_append_only(tmp_path):
    screens = [mk(tmp_path, "20261113T000000Z_a", "1"), mk(tmp_path, "20261113T000200Z_a", "2")]
    path = tmp_path / "labels.jsonl"
    lab = Labeler(list(screens), path, tmp_path / "flags.jsonl")
    assert lab.handle("i")
    assert lab.handle("d")
    assert lab.current() is None
    assert lab.handle("u")
    assert lab.current().id == screens[1].id
    labels = read_labels(path)
    assert labels[screens[0].id]["state"] == "idle_done"
    assert screens[1].id not in labels
    assert len(path.read_text().splitlines()) == 3  # two labels + one tombstone


def test_enter_accepts_identical_previous_screen(tmp_path):
    s1 = mk(tmp_path, "20261113T000000Z_a", "same")
    s2 = mk(tmp_path, "20261113T000200Z_a", "same")
    s3 = mk(tmp_path, "20261113T000400Z_a", "different")
    path = tmp_path / "labels.jsonl"
    lab = Labeler([s1, s2, s3], path, tmp_path / "f.jsonl")
    lab.handle("\r")  # no suggestion yet: ignored
    assert lab.current().id == s1.id
    lab.handle("m")
    assert suggestion(s2, s2.sha256(), lab.labels)["id"] == s1.id
    lab.handle("\r")
    row = read_labels(path)[s2.id]
    assert (row["state"], row["human_reason"], row["same_as"]) == ("needs_human", "machine", s1.id)
    assert suggestion(s3, s3.sha256(), lab.labels) is None


def test_skip_moves_to_end_and_flag_logs(tmp_path):
    s1, s2 = mk(tmp_path, "20261113T000000Z_a", "1"), mk(tmp_path, "20261113T000000Z_b", "2")
    lab = Labeler([s1, s2], tmp_path / "l.jsonl", tmp_path / "f.jsonl")
    lab.handle("!")
    lab.handle("n")
    assert lab.current().id == s2.id
    assert json.loads((tmp_path / "f.jsonl").read_text())["id"] == s1.id


def test_resume_skips_already_labeled(tmp_path):
    s1, s2 = mk(tmp_path, "20261113T000000Z_a", "1"), mk(tmp_path, "20261113T000200Z_a", "2")
    path = tmp_path / "l.jsonl"
    Labeler([s1, s2], path, tmp_path / "f").handle("b")
    assert Labeler([s1, s2], path, tmp_path / "f").current().id == s2.id


def test_staging_note_hidden_until_labeled(tmp_path):
    s1 = mk(tmp_path, "staged_20261113T000000Z_x", "claude\nline1\nline2", source="staged")
    s2 = mk(tmp_path, "staged_20261113T000100Z_x", "bash\n$", source="staged")
    lab = Labeler([s1, s2], tmp_path / "l.jsonl", tmp_path / "f", {s1.id: "trust prompt"})
    out = lab.render(40)
    assert "trust prompt" not in out
    assert "line2" in out and "pane=claude" in out
    assert "\nclaude\n" not in out  # pane-command line is not screen content
    lab.handle("m")
    out = lab.render(40)
    assert "staged as: trust prompt" in out and "you said needs_human/machine" in out


def test_render_and_stats(tmp_path):
    s1 = mk(tmp_path, "staged_20261113T000000Z_x", "claude\nline1\nline2", source="staged")
    lab = Labeler([s1], tmp_path / "l.jsonl", tmp_path / "f")
    lab.handle("m")
    assert "needs_human:machine" in stats(lab.labels)
    assert "staged 1" in stats(lab.labels)
