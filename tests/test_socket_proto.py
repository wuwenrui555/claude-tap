import json
import socket
import threading
import time

from claude_tap.socket_proto import (
    decode_response,
    encode_request,
    try_socket_decision,
)


def test_encode_request_round_trip():
    payload = {"request_id": "r-1", "tool_name": "Bash"}
    data = encode_request(payload)
    assert data.endswith(b"\n")
    assert json.loads(data.decode("utf-8")) == payload


def test_encode_request_unicode():
    payload = {"prompt": "你好"}
    data = encode_request(payload)
    assert json.loads(data.decode("utf-8")) == payload


def test_decode_response_simple():
    line = b'{"request_id":"r-1","decision":{"x":1}}\n'
    obj = decode_response(line)
    assert obj["decision"] == {"x": 1}


def test_try_socket_decision_no_socket(tmp_path):
    sock_path = tmp_path / "missing.sock"
    result = try_socket_decision(sock_path, {"request_id": "r-1"}, timeout=1.0)
    assert result is None


def test_try_socket_decision_round_trip(tmp_path):
    """Set up a fake listener in a background thread; send a request."""
    sock_path = tmp_path / "decision.sock"
    expected_decision = {"hookSpecificOutput": {"permissionDecision": "allow"}}

    server_ready = threading.Event()

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
            response = {
                "request_id": request["request_id"],
                "decision": expected_decision,
            }
            conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
        finally:
            conn.close()
            srv.close()

    t = threading.Thread(target=fake_listener, daemon=True)
    t.start()
    server_ready.wait(timeout=2.0)

    result = try_socket_decision(
        sock_path,
        {"request_id": "r-abc", "tool_name": "Bash"},
        timeout=2.0,
    )
    t.join(timeout=2.0)
    assert result == expected_decision


def test_try_socket_decision_mismatched_request_id(tmp_path):
    """If listener replies with wrong request_id, return None."""
    sock_path = tmp_path / "decision.sock"
    server_ready = threading.Event()

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
            response = {"request_id": "WRONG", "decision": {"x": 1}}
            conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
        finally:
            conn.close()
            srv.close()

    t = threading.Thread(target=fake_listener, daemon=True)
    t.start()
    server_ready.wait(timeout=2.0)

    result = try_socket_decision(
        sock_path,
        {"request_id": "r-abc"},
        timeout=2.0,
    )
    t.join(timeout=2.0)
    assert result is None


def test_try_socket_decision_timeout(tmp_path):
    """Listener accepts but never replies; client times out, returns None."""
    sock_path = tmp_path / "decision.sock"
    server_ready = threading.Event()
    stop = threading.Event()

    def fake_listener():
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock_path))
        srv.listen(1)
        server_ready.set()
        conn, _ = srv.accept()
        try:
            stop.wait(timeout=2.0)  # never replies in time
        finally:
            conn.close()
            srv.close()

    t = threading.Thread(target=fake_listener, daemon=True)
    t.start()
    server_ready.wait(timeout=2.0)

    start = time.time()
    result = try_socket_decision(
        sock_path,
        {"request_id": "r-abc"},
        timeout=0.5,
    )
    elapsed = time.time() - start
    stop.set()
    t.join(timeout=2.0)

    assert result is None
    assert 0.4 < elapsed < 1.5  # roughly the timeout
