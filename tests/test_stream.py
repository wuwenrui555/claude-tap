import asyncio

import pytest

from claude_tap.events import ClaudeInfo, Event, append_jsonl
from claude_tap.stream import EventStream


def make_event(event_type: str = "session_start") -> Event:
    return Event(
        event_type=event_type,
        timestamp="2026-05-08T00:00:00+00:00",
        claude=ClaudeInfo(
            session_id="s",
            transcript_path="/t",
            cwd="/c",
            permission_mode="default",
        ),
        tmux=None,
        surface_id="",
        payload={},
    )


@pytest.mark.asyncio
async def test_stream_yields_new_events(isolated_tap_dir):
    events_file = isolated_tap_dir / "events.jsonl"
    stream = EventStream(path=events_file, poll_interval=0.05)

    received = []

    async def consumer():
        async for ev in stream:
            received.append(ev)
            if len(received) >= 2:
                break

    consumer_task = asyncio.create_task(consumer())
    # Give consumer a moment to start tailing
    await asyncio.sleep(0.1)

    append_jsonl(events_file, make_event("session_start"))
    append_jsonl(events_file, make_event("stop"))

    await asyncio.wait_for(consumer_task, timeout=3.0)
    assert [e["event_type"] for e in received] == ["session_start", "stop"]


@pytest.mark.asyncio
async def test_stream_from_start_replays_history(isolated_tap_dir):
    events_file = isolated_tap_dir / "events.jsonl"
    append_jsonl(events_file, make_event("session_start"))
    append_jsonl(events_file, make_event("user_prompt_submit"))

    stream = EventStream(path=events_file, from_start=True, poll_interval=0.05)
    received = []

    async def consumer():
        async for ev in stream:
            received.append(ev)
            if len(received) >= 2:
                break

    await asyncio.wait_for(consumer(), timeout=2.0)
    assert [e["event_type"] for e in received] == [
        "session_start",
        "user_prompt_submit",
    ]


@pytest.mark.asyncio
async def test_stream_skips_malformed_lines(isolated_tap_dir):
    events_file = isolated_tap_dir / "events.jsonl"
    events_file.parent.mkdir(parents=True, exist_ok=True)
    events_file.write_text("not json\n")
    append_jsonl(events_file, make_event("stop"))

    stream = EventStream(path=events_file, from_start=True, poll_interval=0.05)
    received = []

    async def consumer():
        async for ev in stream:
            received.append(ev)
            if len(received) >= 1:
                break

    await asyncio.wait_for(consumer(), timeout=2.0)
    assert received[0]["event_type"] == "stop"
