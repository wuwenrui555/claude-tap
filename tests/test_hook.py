import json
import socket
import threading

from claude_tap.events import TmuxInfo
from claude_tap.hook import (
    build_event,
    claude_info_from_payload,
    extract_payload,
    normalize_event_name,
    run,
)


def test_normalize_event_name_all_eight():
    assert normalize_event_name("SessionStart") == "session_start"
    assert normalize_event_name("UserPromptSubmit") == "user_prompt_submit"
    assert normalize_event_name("PreToolUse") == "pre_tool_use"
    assert normalize_event_name("PostToolUse") == "post_tool_use"
    assert normalize_event_name("Notification") == "notification"
    assert normalize_event_name("Stop") == "stop"
    assert normalize_event_name("SessionEnd") == "session_end"
    assert normalize_event_name("PermissionRequest") == "permission_request"


def test_extract_payload_pre_tool_use():
    raw = {
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "extra": "ignored",
    }
    p = extract_payload("PreToolUse", raw)
    assert p == {"tool_name": "Bash", "tool_input": {"command": "ls"}}


def test_extract_payload_permission_request_request_id_blank():
    raw = {
        "tool_name": "Bash",
        "tool_input": {"command": "rm"},
        "permission_suggestions": [{"a": 1}],
    }
    p = extract_payload("PermissionRequest", raw)
    assert p == {
        "request_id": "",
        "tool_name": "Bash",
        "tool_input": {"command": "rm"},
        "permission_suggestions": [{"a": 1}],
    }


def test_claude_info_from_payload():
    raw = {
        "session_id": "abc",
        "transcript_path": "/t.jsonl",
        "cwd": "/tmp",
        "permission_mode": "default",
    }
    info = claude_info_from_payload(raw)
    assert info.session_id == "abc"
    assert info.permission_mode == "default"


def test_build_event_envelope(isolated_tap_dir, monkeypatch):
    monkeypatch.setenv("CLAUDE_TAP_SURFACE_ID", "topic_42")
    raw = {
        "session_id": "abc",
        "transcript_path": "/t.jsonl",
        "cwd": "/tmp",
        "permission_mode": "default",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    }
    event = build_event("PreToolUse", raw)
    assert event.event_type == "pre_tool_use"
    assert event.claude.session_id == "abc"
    assert event.surface_id == "topic_42"
    assert event.tmux is None  # not in tmux during test
    assert event.payload["tool_name"] == "Bash"


def test_build_event_with_tmux(isolated_tap_dir, monkeypatch):
    monkeypatch.setenv("CLAUDE_TAP_TMUX_SESSION_NAME", "work")
    monkeypatch.setenv("CLAUDE_TAP_TMUX_WINDOW_ID", "@7")
    monkeypatch.setenv("CLAUDE_TAP_TMUX_PANE_ID", "%12")
    raw = {
        "session_id": "abc",
        "transcript_path": "/t.jsonl",
        "cwd": "/tmp",
        "permission_mode": "default",
    }
    event = build_event("SessionStart", raw)
    assert event.tmux == TmuxInfo(session_name="work", window_id="@7", pane_id="%12")


def test_run_non_permission_returns_empty_object(isolated_tap_dir):
    raw = json.dumps(
        {
            "session_id": "abc",
            "transcript_path": "/t.jsonl",
            "cwd": "/tmp",
            "permission_mode": "default",
            "prompt": "hello",
        }
    )
    output = run("UserPromptSubmit", raw)
    assert output == "{}"

    events_file = isolated_tap_dir / "events.jsonl"
    assert events_file.exists()
    line = events_file.read_text().strip()
    parsed = json.loads(line)
    assert parsed["event_type"] == "user_prompt_submit"
    assert parsed["payload"]["prompt"] == "hello"


def test_run_permission_request_no_listener(isolated_tap_dir, monkeypatch):
    monkeypatch.setenv("CLAUDE_TAP_DECISION_TIMEOUT", "0.5")
    raw = json.dumps(
        {
            "session_id": "abc",
            "transcript_path": "/t.jsonl",
            "cwd": "/tmp",
            "permission_mode": "default",
            "tool_name": "Bash",
            "tool_input": {"command": "rm /tmp/foo"},
        }
    )
    output = run("PermissionRequest", raw)
    assert output == "{}"

    line = (isolated_tap_dir / "events.jsonl").read_text().strip()
    parsed = json.loads(line)
    assert parsed["event_type"] == "permission_request"
    assert parsed["payload"]["request_id"].startswith("r-")
    assert parsed["payload"]["tool_name"] == "Bash"


def test_run_permission_request_with_listener(isolated_tap_dir, monkeypatch):
    """Spin up a fake listener that echoes back an allow decision."""
    monkeypatch.setenv("CLAUDE_TAP_DECISION_TIMEOUT", "2.0")

    sock_path = isolated_tap_dir / "decision.sock"
    server_ready = threading.Event()
    captured_request = {}

    def fake_listener():
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock_path))
        srv.listen(1)
        server_ready.set()
        conn, _ = srv.accept()
        try:
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            request = json.loads(data.split(b"\n", 1)[0].decode("utf-8"))
            captured_request.update(request)
            response = {
                "request_id": request["request_id"],
                "decision": {
                    "hookSpecificOutput": {
                        "hookEventName": "PermissionRequest",
                        "decision": {"behavior": "allow"},
                    }
                },
            }
            conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
        finally:
            conn.close()
            srv.close()

    t = threading.Thread(target=fake_listener, daemon=True)
    t.start()
    server_ready.wait(timeout=2.0)

    raw = json.dumps(
        {
            "session_id": "abc",
            "transcript_path": "/t.jsonl",
            "cwd": "/tmp",
            "permission_mode": "default",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
    )
    output = run("PermissionRequest", raw)
    t.join(timeout=2.0)

    parsed = json.loads(output)
    assert parsed["hookSpecificOutput"]["decision"]["behavior"] == "allow"
    assert captured_request["tool_name"] == "Bash"
    assert captured_request["request_id"].startswith("r-")
