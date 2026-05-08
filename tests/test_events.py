import json

from claude_tap.events import (
    SCHEMA_VERSION,
    ClaudeInfo,
    Event,
    TmuxInfo,
    append_jsonl,
    now_isoformat,
)


def make_event(**overrides) -> Event:
    base = dict(
        event_type="session_start",
        timestamp="2026-05-08T10:34:52.627123+00:00",
        claude=ClaudeInfo(
            session_id="ab7f420b",
            transcript_path="/tmp/t.jsonl",
            cwd="/tmp",
            permission_mode="default",
        ),
        tmux=TmuxInfo(session_name="work", window_id="@7", pane_id="%12"),
        surface_id="",
        payload={},
    )
    base.update(overrides)
    return Event(**base)


def test_to_jsonl_round_trips():
    e = make_event()
    line = e.to_jsonl()
    assert line.endswith("\n")
    obj = json.loads(line)
    assert obj["schema_version"] == SCHEMA_VERSION
    assert obj["event_type"] == "session_start"
    assert obj["claude"]["session_id"] == "ab7f420b"
    assert obj["tmux"]["window_id"] == "@7"
    assert obj["surface_id"] == ""


def test_to_jsonl_with_null_tmux():
    e = make_event(tmux=None)
    obj = json.loads(e.to_jsonl())
    assert obj["tmux"] is None


def test_to_jsonl_preserves_unicode():
    e = make_event(payload={"prompt": "你好世界"})
    obj = json.loads(e.to_jsonl())
    assert obj["payload"]["prompt"] == "你好世界"


def test_now_isoformat_has_tz():
    s = now_isoformat()
    # Must end with +HH:MM offset (UTC produces +00:00)
    after_t = s.split("T", 1)[1]
    assert "+" in after_t or "-" in after_t


def test_append_jsonl_creates_parent_and_writes(tmp_path):
    target = tmp_path / "sub" / "events.jsonl"
    e = make_event()
    append_jsonl(target, e)
    assert target.exists()
    content = target.read_text()
    assert content.count("\n") == 1
    assert json.loads(content) == json.loads(e.to_jsonl())


def test_append_jsonl_appends(tmp_path):
    target = tmp_path / "events.jsonl"
    append_jsonl(target, make_event(event_type="session_start"))
    append_jsonl(target, make_event(event_type="stop"))
    lines = target.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event_type"] == "session_start"
    assert json.loads(lines[1])["event_type"] == "stop"
