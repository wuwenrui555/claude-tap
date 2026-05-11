"""Tests for transcript.read_incremental and parse_entries."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from claude_tap.transcript import (
    PendingTool,
    parse_entries,
    read_incremental,
)


def _line(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False) + "\n"


def _user(text: str, ts: str = "2026-05-10T00:00:00Z") -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "message": {"content": [{"type": "text", "text": text}]},
    }


def _assistant_text(text: str, ts: str = "2026-05-10T00:00:00Z") -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {"content": [{"type": "text", "text": text}]},
    }


def _assistant_tool_use(
    tool_id: str, name: str, inp: dict, ts: str = "2026-05-10T00:00:00Z"
) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": inp}]
        },
    }


def _user_tool_result(
    tool_id: str,
    content,
    is_error: bool = False,
    ts: str = "2026-05-10T00:00:00Z",
) -> dict:
    block: dict = {"type": "tool_result", "tool_use_id": tool_id, "content": content}
    if is_error:
        block["is_error"] = True
    return {
        "type": "user",
        "timestamp": ts,
        "message": {"content": [block]},
    }


# ---------- parse_entries: shape coverage ----------


def test_parse_user_text_message():
    msgs, _, _ = parse_entries([_user("hi there")], "sess1")
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert msgs[0].text == "hi there"
    assert msgs[0].content_type == "text"
    assert msgs[0].session_id == "sess1"


def test_parse_assistant_text_message():
    msgs, _, _ = parse_entries([_assistant_text("hello")], "sess1")
    assert msgs[0].role == "assistant"
    assert msgs[0].content_type == "text"
    assert msgs[0].text == "hello"


def test_parse_tool_use_emits_summary():
    msgs, pending, _ = parse_entries(
        [_assistant_tool_use("toolu_1", "Read", {"file_path": "foo.py"})], "sess1"
    )
    assert len(msgs) == 1
    assert msgs[0].content_type == "tool_use"
    assert msgs[0].tool_use_id == "toolu_1"
    assert msgs[0].tool_name == "Read"
    assert "Read" in msgs[0].text
    assert "foo.py" in msgs[0].text
    assert "toolu_1" in pending


def test_parse_tool_use_then_result_pairs():
    entries = [
        _assistant_tool_use("toolu_1", "Read", {"file_path": "foo.py"}),
        _user_tool_result("toolu_1", "line1\nline2\nline3"),
    ]
    msgs, pending, _ = parse_entries(entries, "sess1")
    types = [(m.content_type, m.tool_name) for m in msgs]
    assert types == [("tool_use", "Read"), ("tool_result", "Read")]
    # tool_result message should have the Read line-count footer
    assert "Read 3 lines" in msgs[1].text
    # pending cleared
    assert "toolu_1" not in pending


def test_pending_tools_carry_over_across_calls():
    # Round 1: only tool_use arrives
    msgs1, pending1, _ = parse_entries(
        [_assistant_tool_use("toolu_1", "Read", {"file_path": "x.py"})], "sess1"
    )
    assert len(msgs1) == 1
    assert "toolu_1" in pending1

    # Round 2: tool_result arrives, with pending threaded back in
    msgs2, pending2, _ = parse_entries(
        [_user_tool_result("toolu_1", "single-line data")],
        "sess1",
        pending_tools=pending1,
    )
    assert len(msgs2) == 1
    assert msgs2[0].content_type == "tool_result"
    assert msgs2[0].tool_name == "Read"
    assert "Read 1 lines" in msgs2[0].text
    assert "toolu_1" not in pending2


def test_thinking_blocks_are_dropped():
    entry = {
        "type": "assistant",
        "timestamp": "2026-05-10T00:00:00Z",
        "message": {
            "content": [
                {"type": "thinking", "thinking": "internal CoT"},
                {"type": "text", "text": "visible reply"},
            ]
        },
    }
    msgs, _, _ = parse_entries([entry], "sess1")
    types = [m.content_type for m in msgs]
    assert "thinking" not in types
    assert types == ["text"]


def test_interrupted_tool_result():
    entries = [
        _assistant_tool_use("toolu_1", "Bash", {"command": "ls"}),
        _user_tool_result("toolu_1", "[Request interrupted by user for tool use]"),
    ]
    msgs, _, _ = parse_entries(entries, "sess1")
    assert msgs[1].content_type == "tool_result"
    assert "Interrupted" in msgs[1].text


def test_error_tool_result_has_error_marker():
    entries = [
        _assistant_tool_use("toolu_1", "Bash", {"command": "false"}),
        _user_tool_result("toolu_1", "exit code 1", is_error=True),
    ]
    msgs, _, _ = parse_entries(entries, "sess1")
    assert msgs[1].content_type == "tool_result"
    assert "Error" in msgs[1].text


def test_tool_result_with_image():
    png_bytes = b"\x89PNG\r\n\x1a\n"  # not a real png but enough for round-trip
    encoded = base64.b64encode(png_bytes).decode("ascii")
    img_block = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": encoded},
    }
    content = [{"type": "text", "text": "see image"}, img_block]
    entries = [
        _assistant_tool_use("toolu_1", "Read", {"file_path": "screenshot.png"}),
        _user_tool_result("toolu_1", content),
    ]
    msgs, _, _ = parse_entries(entries, "sess1")
    assert msgs[1].image_data == [("image/png", png_bytes)]


def test_exit_plan_mode_emits_plan_text_first():
    entries = [
        _assistant_tool_use("toolu_1", "ExitPlanMode", {"plan": "step 1\nstep 2"}),
    ]
    msgs, _, _ = parse_entries(entries, "sess1")
    # Plan text emitted as a separate text message before the tool_use entry
    types = [(m.content_type, m.text[:20]) for m in msgs]
    assert types[0][0] == "text"
    assert "step 1" in msgs[0].text
    assert msgs[1].content_type == "tool_use"
    assert msgs[1].tool_name == "ExitPlanMode"


def test_ask_user_question_input_passthrough():
    entries = [
        _assistant_tool_use(
            "toolu_1",
            "AskUserQuestion",
            {"questions": [{"question": "Yes or no?"}]},
        )
    ]
    msgs, _, _ = parse_entries(entries, "sess1")
    assert msgs[0].input is not None
    assert "questions" in msgs[0].input


def test_non_prompt_tool_input_not_passthrough():
    entries = [
        _assistant_tool_use("toolu_1", "Bash", {"command": "echo hi"}),
    ]
    msgs, _, _ = parse_entries(entries, "sess1")
    assert msgs[0].input is None


def test_local_command_pair_across_entries():
    invoke = {
        "type": "user",
        "timestamp": "2026-05-10T00:00:00Z",
        "message": {
            "content": [{"type": "text", "text": "<command-name>/foo</command-name>"}]
        },
    }
    output = {
        "type": "user",
        "timestamp": "2026-05-10T00:00:01Z",
        "message": {
            "content": [
                {
                    "type": "text",
                    "text": "<local-command-stdout>hello</local-command-stdout>",
                }
            ]
        },
    }
    msgs, _, _ = parse_entries([invoke, output], "sess1")
    assert len(msgs) == 1
    assert msgs[0].content_type == "local_command"
    assert "/foo" in msgs[0].text
    assert "hello" in msgs[0].text


def test_user_text_message_skips_system_tagged_content():
    entry = {
        "type": "user",
        "timestamp": "2026-05-10T00:00:00Z",
        "message": {
            "content": [
                {"type": "text", "text": "<system-reminder>internal</system-reminder>"}
            ]
        },
    }
    msgs, _, _ = parse_entries([entry], "sess1")
    assert msgs == []


def test_unknown_message_type_is_skipped():
    msgs, _, _ = parse_entries([{"type": "summary"}], "sess1")
    assert msgs == []


def test_pending_tool_state_is_not_mutated_on_caller_side():
    # parse_entries must not alter the caller's dict in place.
    pending: dict[str, PendingTool] = {
        "toolu_zombie": PendingTool(summary="**Read**(zombie)", tool_name="Read")
    }
    msgs, new_pending, _ = parse_entries(
        [_user_tool_result("toolu_zombie", "data")],
        "sess1",
        pending_tools=pending,
    )
    # original dict still has the zombie entry
    assert "toolu_zombie" in pending
    # but the returned dict has it consumed
    assert "toolu_zombie" not in new_pending
    assert len(msgs) == 1
    assert msgs[0].tool_name == "Read"


# ---------- read_incremental: I/O coverage ----------


def test_read_incremental_missing_file_returns_empty(tmp_path: Path):
    p = tmp_path / "missing.jsonl"
    msgs, off, pend, lcn = read_incremental(p, "sess1", last_offset=0)
    assert msgs == []
    assert off == 0
    assert pend == {}


def test_read_incremental_full_file_from_zero(tmp_path: Path):
    p = tmp_path / "session.jsonl"
    p.write_text(
        _line(_user("hi"))
        + _line(_assistant_text("hello"))
        + _line(_assistant_tool_use("toolu_1", "Read", {"file_path": "x.py"}))
        + _line(_user_tool_result("toolu_1", "L1\nL2"))
    )
    msgs, off, pend, _ = read_incremental(p, "sess1", last_offset=0)
    types = [m.content_type for m in msgs]
    assert types == ["text", "text", "tool_use", "tool_result"]
    assert off == p.stat().st_size
    assert pend == {}


def test_read_incremental_only_new_content(tmp_path: Path):
    p = tmp_path / "session.jsonl"
    initial = _line(_user("hi"))
    p.write_text(initial)
    initial_size = p.stat().st_size

    # Read past initial content
    msgs, off, pend, _ = read_incremental(p, "sess1", last_offset=initial_size)
    assert msgs == []
    assert off == initial_size

    # Append more
    with p.open("a") as f:
        f.write(_line(_assistant_text("hello again")))

    msgs2, off2, _, _ = read_incremental(
        p, "sess1", last_offset=off, pending_tools=pend
    )
    assert len(msgs2) == 1
    assert msgs2[0].text == "hello again"
    assert off2 == p.stat().st_size


def test_read_incremental_skips_malformed_lines(tmp_path: Path):
    p = tmp_path / "session.jsonl"
    p.write_text(
        "not json\n"
        + _line(_assistant_text("ok"))
        + "another bad line\n"
        + _line(_user("hello"))
    )
    msgs, _, _, _ = read_incremental(p, "sess1", last_offset=0)
    assert [m.text for m in msgs] == ["ok", "hello"]


def test_read_incremental_handles_truncation(tmp_path: Path):
    p = tmp_path / "session.jsonl"
    p.write_text(_line(_user("first")) + _line(_assistant_text("response")))
    full_size = p.stat().st_size

    # Read all of it
    msgs, off, _, _ = read_incremental(p, "sess1", last_offset=0)
    assert off == full_size
    assert len(msgs) == 2

    # Truncate the file
    p.write_text(_line(_user("brand new")))

    # Next read sees size < last_offset → reset and re-read from 0
    msgs2, off2, pend2, lcn2 = read_incremental(p, "sess1", last_offset=full_size)
    assert len(msgs2) == 1
    assert msgs2[0].text == "brand new"
    assert off2 == p.stat().st_size
    assert pend2 == {}
    assert lcn2 is None


def test_read_incremental_carries_pending_across_reads(tmp_path: Path):
    p = tmp_path / "session.jsonl"
    p.write_text(_line(_assistant_tool_use("toolu_a", "Read", {"file_path": "y.py"})))
    msgs1, off1, pend1, lcn1 = read_incremental(p, "sess1", last_offset=0)
    assert len(msgs1) == 1
    assert "toolu_a" in pend1

    with p.open("a") as f:
        f.write(_line(_user_tool_result("toolu_a", "data")))

    msgs2, _, pend2, _ = read_incremental(
        p, "sess1", last_offset=off1, pending_tools=pend1, last_cmd_name=lcn1
    )
    assert len(msgs2) == 1
    assert msgs2[0].content_type == "tool_result"
    assert msgs2[0].tool_name == "Read"
    assert pend2 == {}
