import json
import os
import socket
import subprocess
import sys
import time

import pytest

from claude_tap.cli import (
    _message_to_jsonl,
    _pretty_message,
    _visual_trim,
    _visual_width,
    build_parser,
    cmd_drift,
    cmd_install,
    cmd_uninstall,
    cmd_version,
)
from claude_tap.config import wrapper_path
from claude_tap.models import ClaudeMessage


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
    args = parser.parse_args(["watch-messages"])
    assert args.cmd == "watch-messages"
    assert args.from_start is False
    assert args.json is False
    args = parser.parse_args(["watch-messages", "--from-start", "--json"])
    assert args.cmd == "watch-messages"
    assert args.from_start is True
    assert args.json is True


def test_pretty_message_three_line_shape():
    msg = ClaudeMessage(
        session_id="abcd1234efgh",
        role="user",
        content_type="text",
        text="hello there",
        timestamp="2026-05-10T14:23:45.123Z",
    )
    out = _pretty_message(msg)
    lines = out.split("\n")
    assert len(lines) == 3
    # First line embeds emit-time inside the separator with a single
    # leading dash so the timestamp lines up at column 2 with the
    # header below.
    assert lines[0].startswith("─ ")
    import re

    assert re.match(r"^─ \d{2}:\d{2}:\d{2}\.\d{3} ─+$", lines[0])
    assert lines[1] == "[ 14:23:45 abcd1234 ] USER"
    assert lines[2] == "hello there"


def test_pretty_message_emit_and_msg_timestamps_align_in_column():
    msg = ClaudeMessage(
        session_id="abcd1234",
        role="user",
        content_type="text",
        text="t",
        timestamp="2026-05-10T14:23:45Z",
    )
    out = _pretty_message(msg)
    lines = out.split("\n")
    # In both lines, the timestamp HH starts at column 2 (after the
    # ``─ `` or ``[ `` prefix).
    assert lines[0][2:4].isdigit()  # first 2 digits of HH
    assert lines[1][2:4].isdigit()
    # And specifically those 2-char windows are HH digits.
    assert lines[0][2:10].count(":") == 2  # HH:MM:SS pattern
    assert lines[1][2:10] == "14:23:45"


def test_pretty_separator_visual_width_matches_setting():
    """Separator visual width tracks CLAUDE_TAP_PRETTY_WIDTH (default 100)."""
    msg = ClaudeMessage(
        session_id="s",
        role="user",
        content_type="text",
        text="t",
        timestamp="2026-05-10T00:00:00Z",
    )
    out1 = _pretty_message(msg)
    sep1 = out1.split("\n")[0]
    assert _visual_width(sep1) == 100


def test_pretty_message_tool_use_includes_tool_name():
    msg = ClaudeMessage(
        session_id="sessabcd",
        role="assistant",
        content_type="tool_use",
        text="**Read**(foo.py)",
        tool_name="Read",
        tool_use_id="toolu_x",
        timestamp="2026-05-10T01:02:03+00:00",
    )
    out = _pretty_message(msg)
    lines = out.split("\n")
    assert len(lines) == 3
    assert lines[1].startswith("[ 01:02:03 sessabcd ]")
    assert "ASSISTANT · tool_use · Read" in lines[1]
    assert lines[2] == "**Read**(foo.py)"


def test_pretty_message_collapses_embedded_newlines():
    msg = ClaudeMessage(
        session_id="sessabcd",
        role="assistant",
        content_type="tool_result",
        text="**Read**(foo.py)\n  ⎿  Read 3 lines",
        tool_name="Read",
    )
    out = _pretty_message(msg)
    lines = out.split("\n")
    # Body line is single-line via json.dumps; original \n becomes literal \n
    assert len(lines) == 3
    assert "\\n" in lines[2]
    assert "Read 3 lines" in lines[2]


def test_pretty_message_long_body_trimmed_ascii():
    long_text = "x" * 500
    msg = ClaudeMessage(
        session_id="sessabcd",
        role="user",
        content_type="text",
        text=long_text,
    )
    out = _pretty_message(msg)
    lines = out.split("\n")
    assert len(lines) == 3
    body = lines[2]
    # Visual width matches configured cap.
    assert _visual_width(body) == 100
    assert body.endswith("...")


def test_pretty_message_long_body_trimmed_cjk_visual_width():
    """50 CJK chars = 100 visual cells; should fit roughly in width=100."""
    msg = ClaudeMessage(
        session_id="sessabcd",
        role="user",
        content_type="text",
        text="测" * 200,
    )
    out = _pretty_message(msg)
    body = out.split("\n")[2]
    assert _visual_width(body) <= 100
    # ... and close to it (within 1 cell — odd-cell remainders happen
    # because each CJK char is 2 cells and trim is integer-bounded).
    assert _visual_width(body) >= 99
    assert body.endswith("...")


def test_pretty_width_env_override_trims_body(monkeypatch):
    """CLAUDE_TAP_PRETTY_WIDTH caps both the separator and the body line."""
    monkeypatch.setenv("CLAUDE_TAP_PRETTY_WIDTH", "40")
    msg = ClaudeMessage(
        session_id="sessabcd",
        role="user",
        content_type="text",
        text="x" * 500,
    )
    out = _pretty_message(msg)
    lines = out.split("\n")
    # Separator line trimmed to 40 visual cells too.
    assert _visual_width(lines[0]) == 40
    # Body trimmed to 40 cells.
    body = lines[2]
    assert _visual_width(body) == 40
    assert body.endswith("...")


def test_visual_width_basic():
    assert _visual_width("hello") == 5
    assert _visual_width("中文") == 4  # 2 chars x 2 cells
    assert _visual_width("hi 中") == 5  # "hi " (3) + 中 (2)
    assert _visual_width("") == 0


def test_visual_trim_pure_ascii():
    assert _visual_trim("abcdef", 3) == "abc"
    assert _visual_trim("abcdef", 100) == "abcdef"
    assert _visual_trim("abcdef", 0) == ""


def test_visual_trim_cjk_does_not_split_wide_char():
    # "中文测" has 3 chars × 2 cells = 6 cells.
    # Trimming to 5 cells should keep "中文" (4 cells) and drop "测"
    # entirely — we never emit half a wide char.
    out = _visual_trim("中文测", 5)
    assert out == "中文"
    assert _visual_width(out) == 4


def test_visual_trim_mixed():
    # "a中b" = 1 + 2 + 1 = 4 cells. trim to 3 keeps "a中" (3 cells).
    assert _visual_trim("a中b", 3) == "a中"
    # trim to 2: only "a" fits + half a 中 doesn't, so just "a"
    assert _visual_trim("a中b", 2) == "a"


def test_pretty_message_image_data_inline_marker():
    msg = ClaudeMessage(
        session_id="sessabcd",
        role="assistant",
        content_type="tool_result",
        text="**Read**(screenshot.png)",
        tool_name="Read",
        image_data=[("image/png", b"\x89PNG" + b"x" * 100)],
    )
    out = _pretty_message(msg)
    lines = out.split("\n")
    assert len(lines) == 3
    assert "[+1 image]" in lines[2]


def test_pretty_message_empty_text_still_renders_body():
    msg = ClaudeMessage(
        session_id="sessabcd",
        role="assistant",
        content_type="text",
        text="",
    )
    out = _pretty_message(msg)
    lines = out.split("\n")
    assert len(lines) == 3
    assert lines[2] == ""


def test_pretty_message_missing_timestamp_renders_padding():
    msg = ClaudeMessage(
        session_id="sessabcd",
        role="assistant",
        content_type="text",
        text="ok",
        timestamp=None,
    )
    out = _pretty_message(msg)
    lines = out.split("\n")
    assert len(lines) == 3
    assert "ASSISTANT" in lines[1]
    assert lines[2] == "ok"


def test_pretty_message_includes_tmux_when_present():
    msg = ClaudeMessage(
        session_id="abcd1234efgh",
        role="user",
        content_type="text",
        text="hi",
        timestamp="2026-05-10T14:23:45Z",
        tmux_session_name="ccmux",
        tmux_window_id="@80",
    )
    out = _pretty_message(msg)
    lines = out.split("\n")
    assert lines[1] == "[ 14:23:45 ccmux@80 abcd1234 ] USER"


def test_pretty_message_omits_tmux_when_absent():
    msg = ClaudeMessage(
        session_id="abcd1234efgh",
        role="user",
        content_type="text",
        text="hi",
        timestamp="2026-05-10T14:23:45Z",
    )
    out = _pretty_message(msg)
    lines = out.split("\n")
    assert lines[1] == "[ 14:23:45 abcd1234 ] USER"


def test_message_to_jsonl_text_message():
    msg = ClaudeMessage(
        session_id="s",
        role="assistant",
        content_type="text",
        text="hello",
        timestamp="2026-05-10T00:00:00Z",
    )
    line = _message_to_jsonl(msg)
    parsed = json.loads(line)
    assert parsed["session_id"] == "s"
    assert parsed["role"] == "assistant"
    assert parsed["content_type"] == "text"
    assert parsed["text"] == "hello"
    assert parsed["image_data"] is None


def test_message_to_jsonl_image_data_base64_round_trip():
    raw = b"\x89PNG\r\n\x1a\n binary"
    msg = ClaudeMessage(
        session_id="s",
        role="assistant",
        content_type="tool_result",
        text="",
        tool_use_id="toolu_1",
        tool_name="Read",
        image_data=[("image/png", raw)],
    )
    line = _message_to_jsonl(msg)
    parsed = json.loads(line)
    assert parsed["image_data"] is not None
    media_type, b64 = parsed["image_data"][0]
    assert media_type == "image/png"
    import base64 as _b64

    assert _b64.b64decode(b64) == raw


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
