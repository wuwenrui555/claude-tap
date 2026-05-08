"""Wire protocol for decision.sock + client helper."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any


def encode_request(payload: dict[str, Any]) -> bytes:
    """Encode a hook request as one newline-delimited JSON line."""
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def decode_response(line: bytes) -> dict[str, Any]:
    """Decode one newline-delimited JSON line as response dict."""
    text = line.decode("utf-8").rstrip("\n")
    return json.loads(text)


def _read_until_newline(sock: socket.socket) -> bytes:
    """Read from sock until a \\n is seen. Caller sets timeout via settimeout."""
    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    return b"".join(chunks)


def try_socket_decision(
    sock_path: Path,
    request: dict[str, Any],
    timeout: float,
) -> dict[str, Any] | None:
    """Connect, send request, await matching response.

    Returns the response's `decision` field on success.
    Returns None on any failure path (no socket, refused, timeout,
    malformed JSON, mismatched request_id, OS error).
    """
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(sock_path))
            s.sendall(encode_request(request))
            data = _read_until_newline(s)
        if not data or b"\n" not in data:
            return None
        response = decode_response(data.split(b"\n", 1)[0] + b"\n")
        if response.get("request_id") != request.get("request_id"):
            return None
        return response.get("decision")
    except (
        TimeoutError,
        FileNotFoundError,
        ConnectionRefusedError,
        OSError,
        json.JSONDecodeError,
        ValueError,
    ):
        return None
