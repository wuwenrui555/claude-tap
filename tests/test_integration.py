"""End-to-end: claude-tap-hook subprocess ↔ DecisionListener server."""

import asyncio
import json
import os
import sys

import pytest

from claude_tap.listener import DecisionListener


@pytest.mark.asyncio
async def test_hook_subprocess_no_listener_returns_empty(isolated_tap_dir):
    """No listener bound: hook subprocess writes events.jsonl, prints {}."""
    env = {
        **os.environ,
        "CLAUDE_TAP_DIR": str(isolated_tap_dir),
        "CLAUDE_TAP_DECISION_TIMEOUT": "0.5",
    }
    payload = json.dumps(
        {
            "session_id": "abc",
            "transcript_path": "/tmp/t.jsonl",
            "cwd": "/tmp",
            "permission_mode": "default",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
    )

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "claude_tap.hook",
        "PermissionRequest",
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(
        proc.communicate(payload.encode("utf-8")), timeout=5.0
    )
    assert proc.returncode == 0, f"stderr: {stderr.decode()}"
    assert stdout.decode().strip() == "{}"

    events_file = isolated_tap_dir / "events.jsonl"
    assert events_file.exists()
    lines = events_file.read_text().strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event_type"] == "permission_request"
    assert event["payload"]["request_id"].startswith("r-")


@pytest.mark.asyncio
async def test_hook_subprocess_with_listener_routes_decision(isolated_tap_dir):
    """Listener bound: hook subprocess sends request, gets relayed decision."""
    env = {
        **os.environ,
        "CLAUDE_TAP_DIR": str(isolated_tap_dir),
        "CLAUDE_TAP_DECISION_TIMEOUT": "5.0",
    }
    payload = json.dumps(
        {
            "session_id": "abc",
            "transcript_path": "/tmp/t.jsonl",
            "cwd": "/tmp",
            "permission_mode": "default",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
    )

    async with DecisionListener() as listener:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "claude_tap.hook",
            "PermissionRequest",
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        proc.stdin.write(payload.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        async for req in listener:
            assert req.tool_name == "Bash"
            assert req.tool_input == {"command": "ls"}
            await listener.respond(
                req.request_id,
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PermissionRequest",
                        "decision": {"behavior": "allow"},
                    }
                },
            )
            break

        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        assert proc.returncode == 0, f"stderr: {stderr.decode()}"
        decision = json.loads(stdout.decode())
        assert decision["hookSpecificOutput"]["decision"]["behavior"] == "allow"


@pytest.mark.asyncio
async def test_hook_subprocess_concurrent_requests(isolated_tap_dir):
    """Two hooks fire concurrently; listener routes both correctly."""
    env = {
        **os.environ,
        "CLAUDE_TAP_DIR": str(isolated_tap_dir),
        "CLAUDE_TAP_DECISION_TIMEOUT": "5.0",
    }

    def make_payload(session_id: str, command: str) -> str:
        return json.dumps(
            {
                "session_id": session_id,
                "transcript_path": "/tmp/t.jsonl",
                "cwd": "/tmp",
                "permission_mode": "default",
                "tool_name": "Bash",
                "tool_input": {"command": command},
            }
        )

    async with DecisionListener() as listener:
        proc1 = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "claude_tap.hook",
            "PermissionRequest",
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        proc2 = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "claude_tap.hook",
            "PermissionRequest",
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        proc1.stdin.write(make_payload("s1", "echo 1").encode("utf-8"))
        await proc1.stdin.drain()
        proc1.stdin.close()
        proc2.stdin.write(make_payload("s2", "echo 2").encode("utf-8"))
        await proc2.stdin.drain()
        proc2.stdin.close()

        decisions = {"s1": "allow", "s2": "deny"}
        seen = 0
        async for req in listener:
            d = decisions[req.session_id]
            await listener.respond(
                req.request_id,
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PermissionRequest",
                        "decision": {"behavior": d},
                    }
                },
            )
            seen += 1
            if seen >= 2:
                break

        out1, _ = await asyncio.wait_for(proc1.communicate(), timeout=5.0)
        out2, _ = await asyncio.wait_for(proc2.communicate(), timeout=5.0)
        d1 = json.loads(out1.decode())
        d2 = json.loads(out2.decode())
        assert d1["hookSpecificOutput"]["decision"]["behavior"] == "allow"
        assert d2["hookSpecificOutput"]["decision"]["behavior"] == "deny"


@pytest.mark.asyncio
async def test_full_lifecycle_event_stream(isolated_tap_dir):
    """Several hook subprocesses; EventStream picks them all up in order."""
    from claude_tap.stream import EventStream

    env = {
        **os.environ,
        "CLAUDE_TAP_DIR": str(isolated_tap_dir),
    }

    async def fire_hook(event_name: str, payload_dict: dict) -> None:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "claude_tap.hook",
            event_name,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(
            proc.communicate(json.dumps(payload_dict).encode("utf-8")),
            timeout=5.0,
        )

    base = {
        "session_id": "s",
        "transcript_path": "/t.jsonl",
        "cwd": "/tmp",
        "permission_mode": "default",
    }

    received = []
    stream = EventStream(
        path=isolated_tap_dir / "events.jsonl",
        from_start=True,
        poll_interval=0.05,
    )

    async def collect():
        async for ev in stream:
            received.append(ev)
            if len(received) >= 3:
                stream.close()
                break

    collector_task = asyncio.create_task(collect())
    await asyncio.sleep(0.1)

    await fire_hook("SessionStart", base)
    await fire_hook("UserPromptSubmit", {**base, "prompt": "hi"})
    await fire_hook("Stop", base)

    await asyncio.wait_for(collector_task, timeout=5.0)

    types = [e["event_type"] for e in received]
    assert types == ["session_start", "user_prompt_submit", "stop"]
