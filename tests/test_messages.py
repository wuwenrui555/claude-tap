"""Tests for MessageStream end-to-end orchestration.

Builds a synthetic events.jsonl plus matching transcript files in a
tmp dir and asserts MessageStream yields the expected ClaudeMessage
sequence.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from claude_tap.events import ClaudeInfo, Event, append_jsonl
from claude_tap.messages import MessageStream
from claude_tap.models import ClaudeMessage


def _now_iso() -> str:
    """A timestamp in the future from any plausible subscribe time.

    Tests subscribe at runtime; using a far-future timestamp keeps
    the messages stream's pre-subscribe filter from dropping them.
    """
    return (datetime.now(UTC) + timedelta(hours=1)).isoformat()


def _past_iso() -> str:
    """A timestamp far in the past — filtered out by from_start=False."""
    return "2020-01-01T00:00:00+00:00"


def _ev(
    event_type: str,
    session_id: str,
    transcript_path: str,
    payload: dict | None = None,
    tmux: dict | None = None,
) -> Event:
    from claude_tap.events import TmuxInfo

    tmux_info = (
        TmuxInfo(
            session_name=tmux.get("session_name", ""),
            window_id=tmux.get("window_id", ""),
            pane_id=tmux.get("pane_id", ""),
        )
        if tmux
        else None
    )
    return Event(
        event_type=event_type,
        timestamp=_now_iso(),
        claude=ClaudeInfo(
            session_id=session_id,
            transcript_path=transcript_path,
            cwd="/c",
            permission_mode="default",
        ),
        tmux=tmux_info,
        surface_id="",
        payload=payload or {},
    )


def _transcript_line(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False) + "\n"


def _user_text(text: str, ts: str | None = None) -> dict:
    return {
        "type": "user",
        "timestamp": ts or _now_iso(),
        "message": {"content": [{"type": "text", "text": text}]},
    }


def _assistant_text(text: str, ts: str | None = None) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts or _now_iso(),
        "message": {"content": [{"type": "text", "text": text}]},
    }


def _assistant_tool_use(
    tool_id: str, name: str, inp: dict, ts: str | None = None
) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts or _now_iso(),
        "message": {
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": inp}]
        },
    }


def _user_tool_result(tool_id: str, content, ts: str | None = None) -> dict:
    return {
        "type": "user",
        "timestamp": ts or _now_iso(),
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": content}
            ]
        },
    }


@pytest.mark.asyncio
async def test_message_stream_skips_pre_subscribe_messages_by_default(
    tmp_path: Path,
):
    """from_start=False filters out messages timestamped before subscribe."""
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "session-A.jsonl"

    # Pre-existing transcript content with PAST timestamps — should be skipped
    transcript.write_text(
        _transcript_line(_user_text("old prompt", ts=_past_iso()))
        + _transcript_line(_assistant_text("old reply", ts=_past_iso()))
    )

    stream = MessageStream(events_path_=events_file, poll_interval=0.05)
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 1:
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)  # let consumer start tailing

    # Append a NEW line with a future timestamp — should be delivered
    with transcript.open("a") as f:
        f.write(_transcript_line(_assistant_text("new reply")))

    # Trigger the consumer by appending to events.jsonl
    append_jsonl(events_file, _ev("stop", "sess-A", str(transcript)))

    await asyncio.wait_for(consumer_task, timeout=3.0)
    assert len(received) == 1
    assert received[0].text == "new reply"
    assert received[0].session_id == "sess-A"


@pytest.mark.asyncio
async def test_message_stream_from_start_replays_full_transcript(tmp_path: Path):
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "session-A.jsonl"

    transcript.write_text(
        _transcript_line(_user_text("hi")) + _transcript_line(_assistant_text("hello"))
    )
    append_jsonl(events_file, _ev("stop", "sess-A", str(transcript)))

    stream = MessageStream(
        events_path_=events_file, from_start=True, poll_interval=0.05
    )
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 2:
                break

    await asyncio.wait_for(consumer(), timeout=3.0)
    assert [m.text for m in received] == ["hi", "hello"]


@pytest.mark.asyncio
async def test_message_stream_routes_two_sessions_independently(tmp_path: Path):
    events_file = tmp_path / "events.jsonl"
    t_a = tmp_path / "A.jsonl"
    t_b = tmp_path / "B.jsonl"
    t_a.write_text("")
    t_b.write_text("")

    stream = MessageStream(events_path_=events_file, poll_interval=0.05)
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 2:
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)

    with t_a.open("a") as f:
        f.write(_transcript_line(_assistant_text("from A")))
    with t_b.open("a") as f:
        f.write(_transcript_line(_assistant_text("from B")))

    append_jsonl(events_file, _ev("stop", "sess-A", str(t_a)))
    append_jsonl(events_file, _ev("stop", "sess-B", str(t_b)))

    await asyncio.wait_for(consumer_task, timeout=3.0)
    sids = {m.session_id for m in received}
    assert sids == {"sess-A", "sess-B"}


@pytest.mark.asyncio
async def test_message_stream_tool_pairing_across_events(tmp_path: Path):
    """tool_use is hook-emitted; tool_result comes from transcript paired via pending_tools."""
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(events_path_=events_file, poll_interval=0.05)
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 2:
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)

    # Round 1: tool_use line lands; pre_tool_use hook carries the
    # canonical tool_use info.
    with transcript.open("a") as f:
        f.write(
            _transcript_line(
                _assistant_tool_use("toolu_x", "Read", {"file_path": "main.py"})
            )
        )
    append_jsonl(
        events_file,
        _ev(
            "pre_tool_use",
            "sess-S",
            str(transcript),
            payload={
                "tool_name": "Read",
                "tool_input": {"file_path": "main.py"},
                "tool_use_id": "toolu_x",
            },
        ),
    )

    await asyncio.sleep(0.2)

    # Round 2: tool_result line lands; transcript narrow parse pairs it
    # with the pending_tools entry registered above and emits the
    # ClaudeMessage with the per-tool stats footer.
    with transcript.open("a") as f:
        f.write(_transcript_line(_user_tool_result("toolu_x", "line1\nline2\nline3")))
    append_jsonl(
        events_file,
        _ev(
            "post_tool_use",
            "sess-S",
            str(transcript),
            payload={
                "tool_name": "Read",
                "tool_input": {"file_path": "main.py"},
                "tool_use_id": "toolu_x",
                "tool_response": {},
                "duration_ms": 1,
            },
        ),
    )

    await asyncio.wait_for(consumer_task, timeout=3.0)
    assert received[0].content_type == "tool_use"
    assert received[0].tool_name == "Read"
    assert received[0].tool_use_id == "toolu_x"
    assert "main.py" in received[0].text
    assert received[1].content_type == "tool_result"
    assert received[1].tool_name == "Read"
    assert received[1].tool_use_id == "toolu_x"
    assert "Read 3 lines" in received[1].text


@pytest.mark.asyncio
async def test_pre_tool_use_hook_emits_without_transcript(tmp_path: Path):
    """tool_use ClaudeMessage emitted even when transcript is empty."""
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(events_path_=events_file, poll_interval=0.05)
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 1:
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)

    append_jsonl(
        events_file,
        _ev(
            "pre_tool_use",
            "sess-S",
            str(transcript),
            payload={
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "tool_use_id": "toolu_b",
            },
        ),
    )

    await asyncio.wait_for(consumer_task, timeout=3.0)
    assert received[0].content_type == "tool_use"
    assert received[0].tool_name == "Bash"
    assert received[0].tool_use_id == "toolu_b"
    assert "Bash" in received[0].text and "ls" in received[0].text


@pytest.mark.asyncio
async def test_pre_tool_use_exit_plan_mode_emits_plan_text(tmp_path: Path):
    """ExitPlanMode plan content yields a text ClaudeMessage before tool_use."""
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(events_path_=events_file, poll_interval=0.05)
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 2:
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)

    append_jsonl(
        events_file,
        _ev(
            "pre_tool_use",
            "sess-S",
            str(transcript),
            payload={
                "tool_name": "ExitPlanMode",
                "tool_input": {"plan": "step 1\nstep 2"},
                "tool_use_id": "toolu_p",
            },
        ),
    )

    await asyncio.wait_for(consumer_task, timeout=3.0)
    assert received[0].content_type == "text"
    assert "step 1" in received[0].text
    assert received[1].content_type == "tool_use"
    assert received[1].tool_name == "ExitPlanMode"


@pytest.mark.asyncio
async def test_stop_emits_final_reply_from_hook_payload(tmp_path: Path):
    """Stop's last_assistant_message yields an assistant text ClaudeMessage."""
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(events_path_=events_file, poll_interval=0.05)
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 1:
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)

    append_jsonl(
        events_file,
        _ev(
            "stop",
            "sess-S",
            str(transcript),
            payload={"last_assistant_message": "all done"},
        ),
    )

    await asyncio.wait_for(consumer_task, timeout=3.0)
    assert received[0].content_type == "text"
    assert received[0].role == "assistant"
    assert received[0].text == "all done"


@pytest.mark.asyncio
async def test_stop_dedup_against_transcript_final_reply(tmp_path: Path):
    """Final reply is not double-emitted when transcript has the same text."""
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(events_path_=events_file, poll_interval=0.05)
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 1:
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)

    # Transcript has the final reply ALREADY at hook fire time
    with transcript.open("a") as f:
        f.write(_transcript_line(_assistant_text("all done")))

    append_jsonl(
        events_file,
        _ev(
            "stop",
            "sess-S",
            str(transcript),
            payload={"last_assistant_message": "all done"},
        ),
    )

    await asyncio.wait_for(consumer_task, timeout=3.0)
    # Only one assistant text emitted (from hook, transcript copy dedup'd)
    assistant_texts = [
        m for m in received if m.role == "assistant" and m.content_type == "text"
    ]
    assert len(assistant_texts) == 1
    assert assistant_texts[0].text == "all done"


@pytest.mark.asyncio
async def test_mid_turn_text_emitted_from_transcript(tmp_path: Path):
    """Mid-turn pure-text assistant message is delivered via transcript."""
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(events_path_=events_file, poll_interval=0.05)
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 2:
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)

    # Sequence: mid-turn assistant text appears in transcript before
    # the next pre_tool_use. Reading at pre_tool_use should surface
    # the mid-turn text first, then emit the hook tool_use.
    with transcript.open("a") as f:
        f.write(_transcript_line(_assistant_text("let me look first")))
        f.write(
            _transcript_line(
                _assistant_tool_use("toolu_y", "Read", {"file_path": "y.py"})
            )
        )
    append_jsonl(
        events_file,
        _ev(
            "pre_tool_use",
            "sess-S",
            str(transcript),
            payload={
                "tool_name": "Read",
                "tool_input": {"file_path": "y.py"},
                "tool_use_id": "toolu_y",
            },
        ),
    )

    await asyncio.wait_for(consumer_task, timeout=3.0)
    assert received[0].content_type == "text"
    assert received[0].text == "let me look first"
    assert received[1].content_type == "tool_use"
    assert received[1].tool_use_id == "toolu_y"


@pytest.mark.asyncio
async def test_message_stream_drops_thinking_blocks(tmp_path: Path):
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(
        events_path_=events_file, from_start=True, poll_interval=0.05
    )
    received: list = []

    # Mixed thinking + text in one assistant message
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
    transcript.write_text(_transcript_line(entry))
    append_jsonl(events_file, _ev("stop", "sess-S", str(transcript)))

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 1:
                break

    await asyncio.wait_for(consumer(), timeout=3.0)
    assert all(m.content_type != "thinking" for m in received)
    assert received[0].text == "visible reply"


@pytest.mark.asyncio
async def test_message_stream_missing_transcript_does_not_crash(tmp_path: Path):
    """Hook fires for a session whose transcript file does not exist yet."""
    events_file = tmp_path / "events.jsonl"
    nonexistent = tmp_path / "nope.jsonl"

    stream = MessageStream(events_path_=events_file, poll_interval=0.05)
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)

    # Trigger with a missing transcript first; should be ignored
    append_jsonl(events_file, _ev("session_start", "sess-X", str(nonexistent)))
    await asyncio.sleep(0.2)
    assert received == []

    # Now create the transcript and trigger again; we should get the message
    nonexistent.write_text(_transcript_line(_assistant_text("finally")))
    append_jsonl(events_file, _ev("stop", "sess-X", str(nonexistent)))

    await asyncio.wait_for(consumer_task, timeout=3.0)
    assert len(received) == 1
    assert received[0].text == "finally"


@pytest.mark.asyncio
async def test_user_prompt_submit_emits_from_hook_payload_immediately(
    tmp_path: Path,
):
    """user_prompt_submit yields the prompt before transcript is read."""
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")  # empty: hook payload is the only source

    stream = MessageStream(events_path_=events_file, poll_interval=0.05)
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 1:
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)

    # Submit the user_prompt_submit event with a prompt payload.
    ev = Event(
        event_type="user_prompt_submit",
        timestamp=_now_iso(),
        claude=ClaudeInfo(
            session_id="sess-S",
            transcript_path=str(transcript),
            cwd="/c",
            permission_mode="default",
        ),
        tmux=None,
        surface_id="",
        payload={"prompt": "what is the answer?"},
    )
    append_jsonl(events_file, ev)

    await asyncio.wait_for(consumer_task, timeout=3.0)
    assert len(received) == 1
    assert received[0].role == "user"
    assert received[0].content_type == "text"
    assert received[0].text == "what is the answer?"
    assert received[0].session_id == "sess-S"


@pytest.mark.asyncio
async def test_user_prompt_dedup_against_transcript_user_text(tmp_path: Path):
    """Transcript user-text mirroring a hook prompt is not double-emitted."""
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(events_path_=events_file, poll_interval=0.05)
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 2:  # expect exactly 1 user + 1 assistant
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)

    # Hook fires first
    ev = Event(
        event_type="user_prompt_submit",
        timestamp=_now_iso(),
        claude=ClaudeInfo(
            session_id="sess-S",
            transcript_path=str(transcript),
            cwd="/c",
            permission_mode="default",
        ),
        tmux=None,
        surface_id="",
        payload={"prompt": "hello world"},
    )
    append_jsonl(events_file, ev)

    await asyncio.sleep(0.15)
    # Now transcript catches up: user line + assistant reply
    with transcript.open("a") as f:
        f.write(_transcript_line(_user_text("hello world")))
        f.write(_transcript_line(_assistant_text("hi back")))
    append_jsonl(events_file, _ev("stop", "sess-S", str(transcript)))

    await asyncio.wait_for(consumer_task, timeout=3.0)
    # User prompt counted exactly once (from hook), then assistant reply
    user_msgs = [m for m in received if m.role == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0].text == "hello world"
    assistant_msgs = [m for m in received if m.role == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0].text == "hi back"


@pytest.mark.asyncio
async def test_post_tool_use_scoped_polling_catches_mid_turn_text(tmp_path: Path):
    """Mid-turn text written AFTER post_tool_use should appear within poll_interval, not wait for next hook."""
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(
        events_path_=events_file,
        poll_interval=0.05,
        poll_max_duration=2.0,
    )
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            # Expect at least: tool_use, tool_result, mid-turn text
            if any(
                m.content_type == "text"
                and m.role == "assistant"
                and m.text == "looking at the files now"
                for m in received
            ):
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)

    # Round 1: tool_use line + pre_tool_use hook
    with transcript.open("a") as f:
        f.write(
            _transcript_line(_assistant_tool_use("toolu_x", "Bash", {"command": "ls"}))
        )
    append_jsonl(
        events_file,
        _ev(
            "pre_tool_use",
            "sess-S",
            str(transcript),
            payload={
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "tool_use_id": "toolu_x",
            },
        ),
    )

    await asyncio.sleep(0.15)

    # Round 2: tool_result line + post_tool_use hook → polling starts
    with transcript.open("a") as f:
        f.write(_transcript_line(_user_tool_result("toolu_x", "file1\nfile2")))
    append_jsonl(
        events_file,
        _ev(
            "post_tool_use",
            "sess-S",
            str(transcript),
            payload={
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "tool_use_id": "toolu_x",
                "tool_response": {},
                "duration_ms": 1,
            },
        ),
    )

    # Wait a bit, then write mid-turn text WITHOUT firing any hook.
    # Without scoped polling, this would not appear until a next hook
    # event arrives.
    await asyncio.sleep(0.2)
    with transcript.open("a") as f:
        f.write(_transcript_line(_assistant_text("looking at the files now")))

    # Polling should pick this up within poll_interval (0.05s).
    await asyncio.wait_for(consumer_task, timeout=3.0)

    text_msgs = [
        m for m in received if m.content_type == "text" and m.role == "assistant"
    ]
    assert any(m.text == "looking at the files now" for m in text_msgs)


@pytest.mark.asyncio
async def test_pre_tool_use_also_spawns_scoped_polling(tmp_path: Path):
    """Mid-turn text whose fs flush lags pre_tool_use is caught by polling during the tool's run."""
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(
        events_path_=events_file,
        poll_interval=0.05,
        poll_max_duration=2.0,
    )
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if any(
                m.role == "assistant"
                and m.content_type == "text"
                and m.text == "thought of this between hook and flush"
                for m in received
            ):
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.1)

    # tool_use line lands; pre_tool_use fires. Mid-turn text NOT yet
    # in transcript (simulating fs flush lag).
    with transcript.open("a") as f:
        f.write(
            _transcript_line(
                _assistant_tool_use("toolu_p", "Bash", {"command": "sleep 5"})
            )
        )
    append_jsonl(
        events_file,
        _ev(
            "pre_tool_use",
            "sess-S",
            str(transcript),
            payload={
                "tool_name": "Bash",
                "tool_input": {"command": "sleep 5"},
                "tool_use_id": "toolu_p",
            },
        ),
    )
    # Polling should now be active for this session.
    await asyncio.sleep(0.15)

    # AFTER pre_tool_use's transcript read finished, the mid-turn
    # text gets flushed. Without scoped polling on pre_tool_use this
    # would not appear until post_tool_use eventually fires.
    with transcript.open("a") as f:
        f.write(
            _transcript_line(_assistant_text("thought of this between hook and flush"))
        )

    await asyncio.wait_for(consumer_task, timeout=3.0)
    text_msgs = [
        m for m in received if m.content_type == "text" and m.role == "assistant"
    ]
    assert any(m.text == "thought of this between hook and flush" for m in text_msgs)


@pytest.mark.asyncio
async def test_user_prompt_submit_also_spawns_scoped_polling(tmp_path: Path):
    """Polling activates after user_prompt_submit too, catching post-flush content before pre_tool_use."""
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(
        events_path_=events_file,
        poll_interval=0.05,
        poll_max_duration=2.0,
    )
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if any(
                m.role == "assistant"
                and m.content_type == "text"
                and m.text == "first thought after prompt"
                for m in received
            ):
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.1)

    append_jsonl(
        events_file,
        _ev(
            "user_prompt_submit",
            "sess-S",
            str(transcript),
            payload={"prompt": "go"},
        ),
    )
    await asyncio.sleep(0.15)

    # First assistant text written AFTER user_prompt_submit's
    # transcript read; only polling can catch it before any later
    # hook fires.
    with transcript.open("a") as f:
        f.write(_transcript_line(_assistant_text("first thought after prompt")))

    await asyncio.wait_for(consumer_task, timeout=3.0)
    text_msgs = [
        m for m in received if m.content_type == "text" and m.role == "assistant"
    ]
    assert any(m.text == "first thought after prompt" for m in text_msgs)


@pytest.mark.asyncio
async def test_polling_cancelled_when_next_event_arrives(tmp_path: Path):
    """A scoped poll task started by post_tool_use is cancelled by the next event."""
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(
        events_path_=events_file,
        poll_interval=0.05,
        poll_max_duration=10.0,
    )

    async def consumer():
        async for _msg in stream:
            pass

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.1)

    append_jsonl(
        events_file,
        _ev(
            "post_tool_use",
            "sess-S",
            str(transcript),
            payload={
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "tool_use_id": "toolu_y",
                "tool_response": {},
                "duration_ms": 1,
            },
        ),
    )
    await asyncio.sleep(0.1)
    # A poll task should now exist for this session
    assert "sess-S" in stream._poll_tasks
    poll_task = stream._poll_tasks["sess-S"]
    assert not poll_task.done()

    # Next event arrives — poll should be cancelled and removed
    append_jsonl(
        events_file,
        _ev(
            "pre_tool_use",
            "sess-S",
            str(transcript),
            payload={
                "tool_name": "Bash",
                "tool_input": {"command": "pwd"},
                "tool_use_id": "toolu_z",
            },
        ),
    )
    await asyncio.sleep(0.2)

    # The previous poll task is cancelled. A NEW poll task is spawned
    # by pre_tool_use (since pre_tool_use is also in the polling-spawn
    # set), but it's a different task object.
    assert poll_task.done()
    new_task = stream._poll_tasks.get("sess-S")
    assert new_task is not None
    assert new_task is not poll_task

    stream.close()
    with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
        await asyncio.wait_for(consumer_task, timeout=1.0)


@pytest.mark.asyncio
async def test_tmux_info_stamped_on_emitted_messages(tmp_path: Path):
    """tmux session/window from event envelope is stamped on every emitted message."""
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(events_path_=events_file, poll_interval=0.05)
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 1:
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)

    append_jsonl(
        events_file,
        _ev(
            "user_prompt_submit",
            "sess-T",
            str(transcript),
            payload={"prompt": "hello"},
            tmux={"session_name": "ccmux", "window_id": "@80", "pane_id": "%80"},
        ),
    )

    await asyncio.wait_for(consumer_task, timeout=3.0)
    assert received[0].tmux_session_name == "ccmux"
    assert received[0].tmux_window_id == "@80"


@pytest.mark.asyncio
async def test_message_stream_ignores_events_without_session_or_transcript(
    tmp_path: Path,
):
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text(_transcript_line(_assistant_text("hi")))

    stream = MessageStream(
        events_path_=events_file, from_start=True, poll_interval=0.05
    )
    received: list = []

    # Event with empty claude info should be skipped
    bad_event = Event(
        event_type="notification",
        timestamp="2026-05-10T00:00:00+00:00",
        claude=ClaudeInfo(
            session_id="", transcript_path="", cwd="", permission_mode=""
        ),
        tmux=None,
        surface_id="",
        payload={"message": "ping"},
    )
    append_jsonl(events_file, bad_event)
    # Then a good event for sess-S
    append_jsonl(events_file, _ev("stop", "sess-S", str(transcript)))

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 1:
                break

    await asyncio.wait_for(consumer(), timeout=3.0)
    assert received[0].text == "hi"
    assert received[0].session_id == "sess-S"


# ---------------------------------------------------------------------------
# `source` field provenance — added in v0.2.1
# ---------------------------------------------------------------------------


def test_claude_message_default_source_is_transcript():
    """A freshly-constructed ClaudeMessage with no source kwarg defaults
    to ``"transcript"``. transcript.read_incremental relies on this so
    it doesn't need to set the field at every call site."""
    msg = ClaudeMessage(
        session_id="S",
        role="assistant",
        content_type="text",
        text="x",
    )
    assert msg.source == "transcript"


@pytest.mark.asyncio
async def test_user_prompt_submit_hook_emit_marks_source_hook(tmp_path: Path):
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(events_path_=events_file, poll_interval=0.05)
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 1:
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)

    append_jsonl(
        events_file,
        _ev(
            "user_prompt_submit",
            "sess-S",
            str(transcript),
            payload={"prompt": "hello"},
        ),
    )

    await asyncio.wait_for(consumer_task, timeout=3.0)
    assert received[0].role == "user"
    assert received[0].text == "hello"
    assert received[0].source == "hook"


@pytest.mark.asyncio
async def test_pre_tool_use_hook_emit_marks_source_hook(tmp_path: Path):
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(events_path_=events_file, poll_interval=0.05)
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 1:
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)

    append_jsonl(
        events_file,
        _ev(
            "pre_tool_use",
            "sess-S",
            str(transcript),
            payload={
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "tool_use_id": "toolu_a",
            },
        ),
    )

    await asyncio.wait_for(consumer_task, timeout=3.0)
    assert received[0].content_type == "tool_use"
    assert received[0].source == "hook"


@pytest.mark.asyncio
async def test_pre_tool_use_exit_plan_mode_plan_text_marks_source_hook(
    tmp_path: Path,
):
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(events_path_=events_file, poll_interval=0.05)
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 2:
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)

    append_jsonl(
        events_file,
        _ev(
            "pre_tool_use",
            "sess-S",
            str(transcript),
            payload={
                "tool_name": "ExitPlanMode",
                "tool_input": {"plan": "step 1"},
                "tool_use_id": "toolu_p",
            },
        ),
    )

    await asyncio.wait_for(consumer_task, timeout=3.0)
    # First record: plan body (text). Second: the tool_use itself.
    assert received[0].content_type == "text"
    assert received[0].source == "hook"
    assert received[1].content_type == "tool_use"
    assert received[1].source == "hook"


@pytest.mark.asyncio
async def test_stop_final_reply_marks_source_hook(tmp_path: Path):
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(events_path_=events_file, poll_interval=0.05)
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 1:
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)

    append_jsonl(
        events_file,
        _ev(
            "stop",
            "sess-S",
            str(transcript),
            payload={"last_assistant_message": "all done"},
        ),
    )

    await asyncio.wait_for(consumer_task, timeout=3.0)
    assert received[0].role == "assistant"
    assert received[0].text == "all done"
    assert received[0].source == "hook"


@pytest.mark.asyncio
async def test_mid_turn_text_from_transcript_marks_source_transcript(tmp_path: Path):
    """Mid-turn assistant text comes from transcript and must be flagged so.

    This is the consumer-side discriminator for "is this a final reply or
    mid-turn narration?": role=assistant + content_type=text + source=transcript
    is mid-turn; source=hook is final reply.
    """
    events_file = tmp_path / "events.jsonl"
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("")

    stream = MessageStream(events_path_=events_file, poll_interval=0.05)
    received: list = []

    async def consumer():
        async for msg in stream:
            received.append(msg)
            if len(received) >= 2:
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.15)

    with transcript.open("a") as f:
        f.write(_transcript_line(_assistant_text("let me look first")))
        f.write(
            _transcript_line(
                _assistant_tool_use("toolu_y", "Read", {"file_path": "y.py"})
            )
        )
    append_jsonl(
        events_file,
        _ev(
            "pre_tool_use",
            "sess-S",
            str(transcript),
            payload={
                "tool_name": "Read",
                "tool_input": {"file_path": "y.py"},
                "tool_use_id": "toolu_y",
            },
        ),
    )

    await asyncio.wait_for(consumer_task, timeout=3.0)
    # received[0] = transcript mid-turn text
    # received[1] = hook tool_use (from pre_tool_use payload)
    assert received[0].content_type == "text"
    assert received[0].text == "let me look first"
    assert received[0].source == "transcript"
    assert received[1].content_type == "tool_use"
    assert received[1].source == "hook"
