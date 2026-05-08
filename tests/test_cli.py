import json
import os
import socket
import subprocess
import sys
import time

import pytest

from claude_tap.cli import (
    build_parser,
    cmd_drift,
    cmd_install,
    cmd_uninstall,
    cmd_version,
)
from claude_tap.config import wrapper_path


class _Args:
    pass


def test_install_creates_wrapper(isolated_tap_dir, capsys):
    args = _Args()
    rc = cmd_install(args)
    assert rc == 0
    p = wrapper_path()
    assert p.exists()
    assert oct(p.stat().st_mode)[-3:] == "755"
    body = p.read_text()
    assert body.startswith("#!/usr/bin/env bash")


def test_uninstall_removes_wrapper(isolated_tap_dir):
    cmd_install(_Args())
    p = wrapper_path()
    assert p.exists()

    rc = cmd_uninstall(_Args())
    assert rc == 0
    assert not p.exists()


def test_uninstall_when_not_installed(isolated_tap_dir):
    rc = cmd_uninstall(_Args())
    assert rc == 0


def test_drift_no_log(isolated_tap_dir, capsys):
    """`claude-tap drift` with no drift.log present says so."""
    args = _Args()
    args.raw = False
    rc = cmd_drift(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "No drift detected" in out


def test_drift_summary(isolated_tap_dir, capsys):
    """`claude-tap drift` summarizes by (event, kind, field)."""
    log = isolated_tap_dir / "drift.log"
    log.write_text(
        "2026-05-08T10:00:00+00:00 | SessionEnd | MISSING | end_reason | seen=1\n"
        "2026-05-08T11:00:00+00:00 | SessionEnd | MISSING | end_reason | seen=1\n"
        "2026-05-08T12:00:00+00:00 | PreToolUse | UNKNOWN | new_field | seen=1\n"
    )
    args = _Args()
    args.raw = False
    rc = cmd_drift(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "2 unique drift anomalies" in out
    assert "MISSING" in out
    assert "end_reason" in out
    assert "count=2" in out  # SessionEnd|MISSING|end_reason appears twice
    assert "count=1" in out  # PreToolUse|UNKNOWN|new_field appears once


def test_drift_raw(isolated_tap_dir, capsys):
    """`claude-tap drift --raw` prints the file verbatim."""
    log = isolated_tap_dir / "drift.log"
    body = "2026-05-08T10:00:00+00:00 | SessionEnd | MISSING | end_reason | seen=1\n"
    log.write_text(body)
    args = _Args()
    args.raw = True
    rc = cmd_drift(args)
    assert rc == 0
    assert capsys.readouterr().out == body


def test_version_prints(capsys):
    rc = cmd_version(_Args())
    assert rc == 0
    out = capsys.readouterr().out.strip()
    from claude_tap import __version__

    assert out == __version__


def test_parser_has_subcommands():
    parser = build_parser()
    args = parser.parse_args(["install"])
    assert args.cmd == "install"
    args = parser.parse_args(["watch", "--json"])
    assert args.cmd == "watch"
    assert args.json is True
    args = parser.parse_args(["bridge", "--auto", "allow"])
    assert args.cmd == "bridge"
    assert args.auto == "allow"


def test_bridge_auto_allow_round_trip(isolated_tap_dir):
    """Spawn `claude-tap bridge --auto allow` as subprocess; send a hook req."""
    env = {**os.environ, "CLAUDE_TAP_DIR": str(isolated_tap_dir)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "claude_tap.cli", "bridge", "--auto", "allow"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    sock_path = isolated_tap_dir / "decision.sock"
    # Wait for socket to appear
    for _ in range(50):
        if sock_path.exists():
            break
        time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("bridge did not bind socket")

    # Send a fake request, expect "allow" response.
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(3.0)
    s.connect(str(sock_path))
    req = {
        "request_id": "r-1",
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

    proc.terminate()
    proc.wait(timeout=2.0)

    response = json.loads(data.split(b"\n", 1)[0])
    assert response["request_id"] == "r-1"
    assert response["decision"]["hookSpecificOutput"]["decision"]["behavior"] == "allow"
