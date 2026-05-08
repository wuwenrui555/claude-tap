import asyncio
import json
import socket
import threading

import pytest

from claude_tap.listener import DecisionListener, DecisionRequest


@pytest.mark.asyncio
async def test_listener_creates_and_removes_socket(isolated_tap_dir):
    sock_path = isolated_tap_dir / "decision.sock"
    assert not sock_path.exists()

    async with DecisionListener(path=sock_path):
        assert sock_path.exists()

    assert not sock_path.exists()


@pytest.mark.asyncio
async def test_listener_round_trip(isolated_tap_dir):
    sock_path = isolated_tap_dir / "decision.sock"
    barrier = threading.Event()
    result: dict = {}

    def client():
        barrier.wait(timeout=2.0)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(sock_path))
        req = {
            "request_id": "r-test",
            "session_id": "abc",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "permission_suggestions": [],
        }
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        data = b""
        while b"\n" not in data:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        s.close()
        result["response"] = json.loads(data.split(b"\n", 1)[0])

    async with DecisionListener(path=sock_path) as listener:
        client_thread = threading.Thread(target=client, daemon=True)
        client_thread.start()
        barrier.set()

        async for req in listener:
            assert isinstance(req, DecisionRequest)
            assert req.request_id == "r-test"
            assert req.tool_name == "Bash"
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

        await asyncio.to_thread(client_thread.join, 2.0)

    assert result["response"]["request_id"] == "r-test"
    assert (
        result["response"]["decision"]["hookSpecificOutput"]["decision"]["behavior"]
        == "allow"
    )


@pytest.mark.asyncio
async def test_listener_concurrent_requests(isolated_tap_dir):
    """Two concurrent hook clients; listener routes by request_id."""
    sock_path = isolated_tap_dir / "decision.sock"
    ready = threading.Event()
    results: dict = {}

    def client(request_id: str):
        ready.wait(timeout=2.0)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(sock_path))
        req = {
            "request_id": request_id,
            "session_id": "s",
            "tool_name": "Bash",
            "tool_input": {},
            "permission_suggestions": [],
        }
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        data = b""
        while b"\n" not in data:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        s.close()
        results[request_id] = json.loads(data.split(b"\n", 1)[0])

    async with DecisionListener(path=sock_path) as listener:
        t1 = threading.Thread(target=client, args=("r-1",), daemon=True)
        t2 = threading.Thread(target=client, args=("r-2",), daemon=True)
        t1.start()
        t2.start()
        ready.set()

        seen = 0
        async for req in listener:
            behavior = "allow" if req.request_id == "r-1" else "deny"
            decision = {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {"behavior": behavior},
                }
            }
            await listener.respond(req.request_id, decision)
            seen += 1
            if seen >= 2:
                break

        await asyncio.to_thread(t1.join, 2.0)
        await asyncio.to_thread(t2.join, 2.0)

    assert (
        results["r-1"]["decision"]["hookSpecificOutput"]["decision"]["behavior"]
        == "allow"
    )
    assert (
        results["r-2"]["decision"]["hookSpecificOutput"]["decision"]["behavior"]
        == "deny"
    )
