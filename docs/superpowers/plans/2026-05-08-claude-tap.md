# claude-tap v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans-test-first`
> to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Build the standalone `claude-tap` package that wraps Claude Code,
emits structured hook events to `~/.claude-tap/events.jsonl`, and routes
synchronous PermissionRequest decisions through `~/.claude-tap/decision.sock`.

**Architecture:** A bash wrapper (`~/.claude-tap/bin/claude`) injects
`--session-id` and `--settings` (with all 8 hooks) into a real `claude`
invocation when `CLAUDE_TAP_ACTIVE=1`. Each hook fires `claude-tap-hook
<event_name>`, which writes to events.jsonl unconditionally and (for
`PermissionRequest`) tries a unix-socket round-trip with whoever owns
`decision.sock`. Failures fall open to `{}` so Claude's TUI takes over.

**Tech Stack:** Python 3.11+, hatchling build, pytest + pytest-asyncio,
ruff. Zero runtime dependencies. Bash for the wrapper.

---

## File Structure

```
claude-tap/
├── pyproject.toml                                 # Hatchling, console_scripts
├── README.md                                      # Brief, points to spec
├── LICENSE                                        # Apache 2.0
├── .gitignore
├── docs/superpowers/
│   ├── specs/2026-05-08-claude-tap-design.md     # already committed
│   └── plans/2026-05-08-claude-tap.md            # this plan
├── src/claude_tap/
│   ├── __init__.py                                # public API exports
│   ├── _version.py                                # version string
│   ├── config.py                                  # env vars, paths
│   ├── events.py                                  # Event dataclass, append_jsonl
│   ├── tmux.py                                    # read tmux env vars
│   ├── socket_proto.py                            # wire format + try_socket_decision
│   ├── hook.py                                    # claude-tap-hook entry point
│   ├── stream.py                                  # EventStream async iterator
│   ├── listener.py                                # DecisionListener async server
│   ├── wrapper.py                                 # render bash wrapper string
│   └── cli.py                                     # claude-tap CLI
├── tests/
│   ├── conftest.py                                # isolated tmp CLAUDE_TAP_DIR
│   ├── test_config.py
│   ├── test_events.py
│   ├── test_tmux.py
│   ├── test_socket_proto.py
│   ├── test_hook.py
│   ├── test_stream.py
│   ├── test_listener.py
│   ├── test_wrapper.py
│   ├── test_cli.py
│   └── test_integration.py                        # subprocess hook ↔ listener
└── examples/
    └── sample_consumer.py                         # reference consumer from spec
```

---

## Task 1: Repo skeleton + pyproject + LICENSE

**Files:**
- Create: `pyproject.toml`
- Create: `LICENSE`
- Create: `.gitignore`
- Create: `README.md`
- Create: `src/claude_tap/__init__.py`
- Create: `src/claude_tap/_version.py`
- Create: `tests/conftest.py`
- Create: `tests/test_skeleton.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "claude-tap"
version = "0.1.0"
description = "Wrap Claude Code: structured event stream + sync decision bridge."
readme = "README.md"
requires-python = ">=3.11"
license = { file = "LICENSE" }
keywords = ["claude-code", "hooks", "events", "instrumentation", "tmux"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Terminals",
    "Topic :: Software Development :: Libraries",
    "License :: OSI Approved :: Apache Software License",
]
dependencies = []

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.8.0",
]

[project.scripts]
claude-tap = "claude_tap.cli:main"
claude-tap-hook = "claude_tap.hook:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/claude_tap"]

[tool.ruff]
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
ignore = ["E501"]

[tool.ruff.format]
quote-style = "double"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Write `LICENSE`** (Apache 2.0; copy from `claude-code-state/LICENSE`)

```bash
cp /mnt/nfs/home/wenruiwu/ccmux/claude-code-state/LICENSE /mnt/nfs/home/wenruiwu/ccmux/claude-tap/LICENSE
```

- [ ] **Step 3: Write `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
*.egg-info/
build/
dist/
.venv/
```

- [ ] **Step 4: Write minimal `README.md`**

```markdown
# claude-tap

Wrap Claude Code: structured event stream + synchronous decision bridge.

See `docs/superpowers/specs/2026-05-08-claude-tap-design.md` for the design.

## Status

v0.1 in development.
```

- [ ] **Step 5: Write `src/claude_tap/_version.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 6: Write `src/claude_tap/__init__.py`** (placeholder, real exports added in Task 11)

```python
"""claude-tap: Claude Code → structured events + decision bridge."""
from ._version import __version__

__all__ = ["__version__"]
```

- [ ] **Step 7: Write `tests/conftest.py`**

```python
"""Shared pytest fixtures."""
import os
import pytest


@pytest.fixture
def isolated_tap_dir(tmp_path, monkeypatch):
    """Override CLAUDE_TAP_DIR to a fresh tmp dir for the test."""
    monkeypatch.setenv("CLAUDE_TAP_DIR", str(tmp_path))
    # Clear any tmux-related env that could leak in
    for var in [
        "CLAUDE_TAP_TMUX_SESSION_NAME",
        "CLAUDE_TAP_TMUX_WINDOW_ID",
        "CLAUDE_TAP_TMUX_PANE_ID",
        "CLAUDE_TAP_SURFACE_ID",
        "CLAUDE_TAP_DECISION_TIMEOUT",
    ]:
        monkeypatch.delenv(var, raising=False)
    return tmp_path
```

- [ ] **Step 8: Write `tests/test_skeleton.py`**

```python
def test_import_package():
    import claude_tap
    assert claude_tap.__version__ == "0.1.0"
```

- [ ] **Step 9: Install package in editable mode**

```bash
cd /mnt/nfs/home/wenruiwu/ccmux/claude-tap
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Expected: package installed, `claude-tap` and `claude-tap-hook` appear in `.venv/bin/`.

- [ ] **Step 10: Run skeleton test**

```bash
pytest tests/test_skeleton.py -v
```

Expected: 1 passed.

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml LICENSE .gitignore README.md src/ tests/
git commit -m "feat(skeleton): pyproject + minimal package + smoke test"
```

---

## Task 2: Config — paths and env vars

**Files:**
- Create: `src/claude_tap/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:

```python
from pathlib import Path

import pytest

from claude_tap.config import (
    DEFAULT_DECISION_TIMEOUT,
    claude_tap_dir,
    decision_sock_path,
    decision_timeout,
    events_path,
    surface_id,
    wrapper_path,
)


def test_default_dir(monkeypatch):
    monkeypatch.delenv("CLAUDE_TAP_DIR", raising=False)
    assert claude_tap_dir() == Path.home() / ".claude-tap"


def test_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_TAP_DIR", str(tmp_path))
    assert claude_tap_dir() == tmp_path


def test_dir_expands_user(monkeypatch):
    monkeypatch.setenv("CLAUDE_TAP_DIR", "~/foo")
    assert claude_tap_dir() == Path.home() / "foo"


def test_derived_paths(isolated_tap_dir):
    assert events_path() == isolated_tap_dir / "events.jsonl"
    assert decision_sock_path() == isolated_tap_dir / "decision.sock"
    assert wrapper_path() == isolated_tap_dir / "bin" / "claude"


def test_surface_id_default(isolated_tap_dir):
    assert surface_id() == ""


def test_surface_id_set(isolated_tap_dir, monkeypatch):
    monkeypatch.setenv("CLAUDE_TAP_SURFACE_ID", "topic_42")
    assert surface_id() == "topic_42"


def test_decision_timeout_default(isolated_tap_dir):
    assert decision_timeout() == DEFAULT_DECISION_TIMEOUT


def test_decision_timeout_override(isolated_tap_dir, monkeypatch):
    monkeypatch.setenv("CLAUDE_TAP_DECISION_TIMEOUT", "30")
    assert decision_timeout() == 30.0


def test_decision_timeout_invalid_falls_back(isolated_tap_dir, monkeypatch):
    monkeypatch.setenv("CLAUDE_TAP_DECISION_TIMEOUT", "abc")
    assert decision_timeout() == DEFAULT_DECISION_TIMEOUT
```

- [ ] **Step 2: Run test, verify it fails**

```bash
pytest tests/test_config.py -v
```

Expected: ImportError on `claude_tap.config`.

- [ ] **Step 3: Write minimal `src/claude_tap/config.py`**

```python
"""Environment variable resolution and path helpers."""
import os
from pathlib import Path

DEFAULT_DIR = "~/.claude-tap"
DEFAULT_DECISION_TIMEOUT = 120.0


def claude_tap_dir() -> Path:
    raw = os.environ.get("CLAUDE_TAP_DIR", DEFAULT_DIR)
    return Path(raw).expanduser()


def events_path() -> Path:
    return claude_tap_dir() / "events.jsonl"


def decision_sock_path() -> Path:
    return claude_tap_dir() / "decision.sock"


def wrapper_path() -> Path:
    return claude_tap_dir() / "bin" / "claude"


def surface_id() -> str:
    return os.environ.get("CLAUDE_TAP_SURFACE_ID", "")


def decision_timeout() -> float:
    raw = os.environ.get("CLAUDE_TAP_DECISION_TIMEOUT", "")
    if not raw:
        return DEFAULT_DECISION_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_DECISION_TIMEOUT
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
pytest tests/test_config.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/claude_tap/config.py tests/test_config.py
git commit -m "feat(config): env-driven paths and timeout"
```

---

## Task 3: Event schema + atomic JSONL append

**Files:**
- Create: `src/claude_tap/events.py`
- Create: `tests/test_events.py`

- [ ] **Step 1: Write the failing test**

`tests/test_events.py`:

```python
import json
from pathlib import Path

from claude_tap.events import (
    SCHEMA_VERSION,
    ClaudeInfo,
    Event,
    TmuxInfo,
    append_jsonl,
    now_isoformat,
)


def make_event(**overrides) -> Event:
    base = dict(
        event_type="session_start",
        timestamp="2026-05-08T10:34:52.627123+00:00",
        claude=ClaudeInfo(
            session_id="ab7f420b",
            transcript_path="/tmp/t.jsonl",
            cwd="/tmp",
            permission_mode="default",
        ),
        tmux=TmuxInfo(session_name="work", window_id="@7", pane_id="%12"),
        surface_id="",
        payload={},
    )
    base.update(overrides)
    return Event(**base)


def test_to_jsonl_round_trips():
    e = make_event()
    line = e.to_jsonl()
    assert line.endswith("\n")
    obj = json.loads(line)
    assert obj["schema_version"] == SCHEMA_VERSION
    assert obj["event_type"] == "session_start"
    assert obj["claude"]["session_id"] == "ab7f420b"
    assert obj["tmux"]["window_id"] == "@7"
    assert obj["surface_id"] == ""


def test_to_jsonl_with_null_tmux():
    e = make_event(tmux=None)
    obj = json.loads(e.to_jsonl())
    assert obj["tmux"] is None


def test_to_jsonl_preserves_unicode():
    e = make_event(payload={"prompt": "你好世界"})
    obj = json.loads(e.to_jsonl())
    assert obj["payload"]["prompt"] == "你好世界"


def test_now_isoformat_has_tz():
    s = now_isoformat()
    # Must end with +HH:MM offset (UTC produces +00:00)
    assert "+" in s or "-" in s.split("T")[1]


def test_append_jsonl_creates_parent_and_writes(tmp_path):
    target = tmp_path / "sub" / "events.jsonl"
    e = make_event()
    append_jsonl(target, e)
    assert target.exists()
    content = target.read_text()
    assert content.count("\n") == 1
    assert json.loads(content) == json.loads(e.to_jsonl())


def test_append_jsonl_appends(tmp_path):
    target = tmp_path / "events.jsonl"
    append_jsonl(target, make_event(event_type="session_start"))
    append_jsonl(target, make_event(event_type="stop"))
    lines = target.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event_type"] == "session_start"
    assert json.loads(lines[1])["event_type"] == "stop"
```

- [ ] **Step 2: Run, verify fails**

```bash
pytest tests/test_events.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `src/claude_tap/events.py`**

```python
"""Event schema and atomic JSONL append."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ClaudeInfo:
    session_id: str
    transcript_path: str
    cwd: str
    permission_mode: str


@dataclass(frozen=True)
class TmuxInfo:
    session_name: str
    window_id: str
    pane_id: str


@dataclass(frozen=True)
class Event:
    event_type: str
    timestamp: str
    claude: ClaudeInfo
    tmux: Optional[TmuxInfo]
    surface_id: str
    payload: dict[str, Any]
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "claude": asdict(self.claude),
            "tmux": asdict(self.tmux) if self.tmux is not None else None,
            "surface_id": self.surface_id,
            "payload": self.payload,
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False) + "\n"


def now_isoformat() -> str:
    """Current time in ISO 8601 with UTC offset."""
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, event: Event) -> None:
    """Append one event line atomically.

    O_APPEND on POSIX guarantees single-write atomicity for writes ≤
    PIPE_BUF (≥ 4 KB on Linux). Lines are kept short by the
    schema; payload extraction never embeds full transcript content.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = event.to_jsonl().encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/test_events.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/claude_tap/events.py tests/test_events.py
git commit -m "feat(events): event schema + atomic JSONL append"
```

---

## Task 4: Tmux env reader

**Files:**
- Create: `src/claude_tap/tmux.py`
- Create: `tests/test_tmux.py`

- [ ] **Step 1: Write the failing test**

`tests/test_tmux.py`:

```python
from claude_tap.events import TmuxInfo
from claude_tap.tmux import read_tmux_info_from_env


def test_no_env_returns_none(isolated_tap_dir):
    assert read_tmux_info_from_env() is None


def test_all_env_set(isolated_tap_dir, monkeypatch):
    monkeypatch.setenv("CLAUDE_TAP_TMUX_SESSION_NAME", "work")
    monkeypatch.setenv("CLAUDE_TAP_TMUX_WINDOW_ID", "@7")
    monkeypatch.setenv("CLAUDE_TAP_TMUX_PANE_ID", "%12")
    info = read_tmux_info_from_env()
    assert info == TmuxInfo(session_name="work", window_id="@7", pane_id="%12")


def test_partial_env(isolated_tap_dir, monkeypatch):
    monkeypatch.setenv("CLAUDE_TAP_TMUX_WINDOW_ID", "@7")
    info = read_tmux_info_from_env()
    assert info == TmuxInfo(session_name="", window_id="@7", pane_id="")
```

- [ ] **Step 2: Run, verify fails**

```bash
pytest tests/test_tmux.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `src/claude_tap/tmux.py`**

```python
"""Read tmux context that the wrapper exported into env vars."""
import os
from typing import Optional

from .events import TmuxInfo


def read_tmux_info_from_env() -> Optional[TmuxInfo]:
    """Build a TmuxInfo from CLAUDE_TAP_TMUX_* env vars.

    The wrapper is responsible for populating these vars. When all
    three are absent, the wrapper either was not invoked or did not
    detect tmux — return None so the event records `tmux: null`.
    """
    session_name = os.environ.get("CLAUDE_TAP_TMUX_SESSION_NAME", "")
    window_id = os.environ.get("CLAUDE_TAP_TMUX_WINDOW_ID", "")
    pane_id = os.environ.get("CLAUDE_TAP_TMUX_PANE_ID", "")
    if not session_name and not window_id and not pane_id:
        return None
    return TmuxInfo(
        session_name=session_name,
        window_id=window_id,
        pane_id=pane_id,
    )
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/test_tmux.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/claude_tap/tmux.py tests/test_tmux.py
git commit -m "feat(tmux): read tmux info from wrapper-exported env"
```

---

## Task 5: Socket protocol — encoding + try_socket_decision client

**Files:**
- Create: `src/claude_tap/socket_proto.py`
- Create: `tests/test_socket_proto.py`

- [ ] **Step 1: Write the failing test**

`tests/test_socket_proto.py`:

```python
import asyncio
import json
import socket
from pathlib import Path

import pytest

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
    import threading

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
    import threading

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
    import threading
    import time

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
```

- [ ] **Step 2: Run, verify fails**

```bash
pytest tests/test_socket_proto.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `src/claude_tap/socket_proto.py`**

```python
"""Wire protocol for decision.sock + client helper."""
from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any, Optional


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
) -> Optional[dict[str, Any]]:
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
        FileNotFoundError,
        ConnectionRefusedError,
        socket.timeout,
        OSError,
        json.JSONDecodeError,
        ValueError,
    ):
        return None
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/test_socket_proto.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/claude_tap/socket_proto.py tests/test_socket_proto.py
git commit -m "feat(socket-proto): wire format + try_socket_decision client"
```

---

## Task 6: Hook entry point

**Files:**
- Create: `src/claude_tap/hook.py`
- Create: `tests/test_hook.py`

- [ ] **Step 1: Write the failing test**

`tests/test_hook.py`:

```python
import json
import socket
import threading

import pytest

from claude_tap.hook import (
    build_event,
    claude_info_from_payload,
    extract_payload,
    normalize_event_name,
    run,
)
from claude_tap.events import TmuxInfo


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


def test_run_non_permission_returns_empty_object(isolated_tap_dir):
    raw = json.dumps({
        "session_id": "abc",
        "transcript_path": "/t.jsonl",
        "cwd": "/tmp",
        "permission_mode": "default",
        "prompt": "hello",
    })
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
    raw = json.dumps({
        "session_id": "abc",
        "transcript_path": "/t.jsonl",
        "cwd": "/tmp",
        "permission_mode": "default",
        "tool_name": "Bash",
        "tool_input": {"command": "rm /tmp/foo"},
    })
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
                        "permissionDecision": "allow",
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

    raw = json.dumps({
        "session_id": "abc",
        "transcript_path": "/t.jsonl",
        "cwd": "/tmp",
        "permission_mode": "default",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    })
    output = run("PermissionRequest", raw)
    t.join(timeout=2.0)

    parsed = json.loads(output)
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert captured_request["tool_name"] == "Bash"
    assert captured_request["request_id"].startswith("r-")
```

- [ ] **Step 2: Run, verify fails**

```bash
pytest tests/test_hook.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `src/claude_tap/hook.py`**

```python
"""Hook entry point invoked by Claude per registered hook event.

Usage: claude-tap-hook <event_name>

Reads stdin (Claude's hook payload, JSON), writes a normalized event
to events.jsonl, and (for PermissionRequest only) blocks on a unix
socket round-trip with whoever owns decision.sock. Prints the decision
JSON (or {}) to stdout for Claude to read.
"""
from __future__ import annotations

import json
import sys
import uuid
from typing import Any

from .config import (
    decision_sock_path,
    decision_timeout,
    events_path,
    surface_id,
)
from .events import ClaudeInfo, Event, append_jsonl, now_isoformat
from .socket_proto import try_socket_decision
from .tmux import read_tmux_info_from_env

_EVENT_NAME_MAP = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "user_prompt_submit",
    "PreToolUse": "pre_tool_use",
    "PostToolUse": "post_tool_use",
    "Notification": "notification",
    "Stop": "stop",
    "SessionEnd": "session_end",
    "PermissionRequest": "permission_request",
}


def normalize_event_name(claude_event_name: str) -> str:
    return _EVENT_NAME_MAP.get(claude_event_name, claude_event_name.lower())


def claude_info_from_payload(raw: dict[str, Any]) -> ClaudeInfo:
    return ClaudeInfo(
        session_id=raw.get("session_id", ""),
        transcript_path=raw.get("transcript_path", ""),
        cwd=raw.get("cwd", ""),
        permission_mode=raw.get("permission_mode", ""),
    )


def extract_payload(event_name: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Pull event-specific payload out of Claude's hook stdin JSON."""
    if event_name == "UserPromptSubmit":
        return {"prompt": raw.get("prompt", "")}
    if event_name == "PreToolUse":
        return {
            "tool_name": raw.get("tool_name", ""),
            "tool_input": raw.get("tool_input", {}),
        }
    if event_name == "PostToolUse":
        return {
            "tool_name": raw.get("tool_name", ""),
            "tool_input": raw.get("tool_input", {}),
            "tool_response": raw.get("tool_response", {}),
        }
    if event_name == "Notification":
        return {"message": raw.get("message", "")}
    if event_name == "SessionEnd":
        return {"reason": raw.get("reason", "")}
    if event_name == "PermissionRequest":
        return {
            "request_id": "",  # filled in by run()
            "tool_name": raw.get("tool_name", ""),
            "tool_input": raw.get("tool_input", {}),
            "permission_suggestions": raw.get("permission_suggestions", []),
        }
    return {}


def build_event(
    event_name: str,
    raw: dict[str, Any],
    request_id: str = "",
) -> Event:
    payload = extract_payload(event_name, raw)
    if event_name == "PermissionRequest" and request_id:
        payload["request_id"] = request_id
    return Event(
        event_type=normalize_event_name(event_name),
        timestamp=now_isoformat(),
        claude=claude_info_from_payload(raw),
        tmux=read_tmux_info_from_env(),
        surface_id=surface_id(),
        payload=payload,
    )


def _generate_request_id() -> str:
    return "r-" + uuid.uuid4().hex[:12]


def run(event_name: str, stdin_text: str) -> str:
    """Process one hook invocation. Returns stdout text."""
    try:
        raw = json.loads(stdin_text) if stdin_text.strip() else {}
    except json.JSONDecodeError:
        raw = {}

    request_id = _generate_request_id() if event_name == "PermissionRequest" else ""
    event = build_event(event_name, raw, request_id=request_id)

    try:
        append_jsonl(events_path(), event)
    except OSError as e:
        print(f"claude-tap: failed to append events.jsonl: {e}", file=sys.stderr)

    if event_name != "PermissionRequest":
        return "{}"

    decision = try_socket_decision(
        sock_path=decision_sock_path(),
        request=event.payload,
        timeout=decision_timeout(),
    )
    if not decision:
        return "{}"
    return json.dumps(decision, ensure_ascii=False)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: claude-tap-hook <event_name>", file=sys.stderr)
        return 2
    event_name = sys.argv[1]
    stdin_text = sys.stdin.read()
    sys.stdout.write(run(event_name, stdin_text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/test_hook.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/claude_tap/hook.py tests/test_hook.py
git commit -m "feat(hook): claude-tap-hook entry point"
```

---

## Task 7: EventStream — async iterator for consumers

**Files:**
- Create: `src/claude_tap/stream.py`
- Create: `tests/test_stream.py`

> **Implementation note**: v0.1 uses 100ms polling, not inotify. This
> is a deliberate simplicity tradeoff for v0.1 (zero deps, cross-FS
> correctness on edge cases like NFS). v0.2 may switch to inotify if a
> latency requirement emerges. The spec section "Sample consumer"
> currently claims sub-millisecond latency; **update the spec** in this
> task to clarify v0.1 is poll-based.

- [ ] **Step 1: Write the failing test**

`tests/test_stream.py`:

```python
import asyncio
import json

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
    assert [e["event_type"] for e in received] == ["session_start", "user_prompt_submit"]


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
```

- [ ] **Step 2: Run, verify fails**

```bash
pytest tests/test_stream.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `src/claude_tap/stream.py`**

```python
"""EventStream: async iterator over events.jsonl.

v0.1 uses short-interval file polling (default 100ms). The interface is
push-style from the consumer's perspective; `async for ev in EventStream()`
yields each new event as it lands. Latency is bounded by the poll
interval. v0.2 may switch to inotify-backed pushing for sub-millisecond
latency when there is a real requirement.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator, Optional

from .config import events_path

_DEFAULT_POLL_INTERVAL = 0.1  # 100 ms


class EventStream:
    """Async iterator over events.jsonl.

    Usage:
        async for event in EventStream():
            handle(event)
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        from_start: bool = False,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
    ):
        self._path = path or events_path()
        self._from_start = from_start
        self._poll_interval = poll_interval
        self._closed = False

    def close(self) -> None:
        self._closed = True

    async def __aiter__(self) -> AsyncIterator[dict]:
        # Wait for file to exist
        while not self._path.exists() and not self._closed:
            await asyncio.sleep(self._poll_interval)
        if self._closed:
            return

        with open(self._path, "r", encoding="utf-8") as f:
            if not self._from_start:
                f.seek(0, 2)  # end of file

            buf = ""
            while not self._closed:
                line = f.readline()
                if not line:
                    await asyncio.sleep(self._poll_interval)
                    continue
                buf += line
                if buf.endswith("\n"):
                    try:
                        yield json.loads(buf.rstrip("\n"))
                    except json.JSONDecodeError:
                        pass  # skip malformed
                    buf = ""
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/test_stream.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Update spec to reflect v0.1 polling**

Edit `docs/superpowers/specs/2026-05-08-claude-tap-design.md`:

In the sample consumer's docstring, change:

```python
    """Print every event as it arrives.

    EventStream is an async iterator. Internally it uses inotify on
    events.jsonl, so consumption is push-based with sub-millisecond
    latency. From the consumer's perspective, it is just `async for`.
    """
```

to:

```python
    """Print every event as it arrives.

    EventStream is an async iterator. v0.1 polls events.jsonl at 100 ms
    intervals; latency is bounded by that interval. v0.2 may switch to
    inotify-backed push. From the consumer's perspective, it is always
    just `async for`.
    """
```

- [ ] **Step 6: Commit**

```bash
git add src/claude_tap/stream.py tests/test_stream.py docs/superpowers/specs/2026-05-08-claude-tap-design.md
git commit -m "feat(stream): EventStream async iterator (poll-based v0.1)"
```

---

## Task 8: DecisionListener — async server bound to decision.sock

**Files:**
- Create: `src/claude_tap/listener.py`
- Create: `tests/test_listener.py`

- [ ] **Step 1: Write the failing test**

`tests/test_listener.py`:

```python
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

    async def fake_hook(barrier: threading.Event, result: dict):
        # Run a sync socket client in a thread, since the hook side is
        # synchronous in real life.
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
        t = threading.Thread(target=client, daemon=True)
        t.start()
        return t

    barrier = threading.Event()
    result: dict = {}
    async with DecisionListener(path=sock_path) as listener:
        client_thread = await fake_hook(barrier, result)
        barrier.set()

        async for req in listener:
            assert isinstance(req, DecisionRequest)
            assert req.request_id == "r-test"
            assert req.tool_name == "Bash"
            await listener.respond(
                req.request_id,
                {"hookSpecificOutput": {"permissionDecision": "allow"}},
            )
            break

        # Wait for client to receive
        await asyncio.to_thread(client_thread.join, 2.0)

    assert result["response"]["request_id"] == "r-test"
    assert result["response"]["decision"]["hookSpecificOutput"]["permissionDecision"] == "allow"


@pytest.mark.asyncio
async def test_listener_concurrent_requests(isolated_tap_dir):
    """Two concurrent hook clients; listener routes by request_id."""
    sock_path = isolated_tap_dir / "decision.sock"

    def client(request_id: str, decision_value: str, results: dict, ready: threading.Event):
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
        ready = threading.Event()
        results: dict = {}
        t1 = threading.Thread(target=client, args=("r-1", "allow", results, ready), daemon=True)
        t2 = threading.Thread(target=client, args=("r-2", "deny", results, ready), daemon=True)
        t1.start()
        t2.start()
        ready.set()

        seen = 0
        async for req in listener:
            decision = {"hookSpecificOutput": {"permissionDecision": "allow" if req.request_id == "r-1" else "deny"}}
            await listener.respond(req.request_id, decision)
            seen += 1
            if seen >= 2:
                break

        await asyncio.to_thread(t1.join, 2.0)
        await asyncio.to_thread(t2.join, 2.0)

    assert results["r-1"]["decision"]["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert results["r-2"]["decision"]["hookSpecificOutput"]["permissionDecision"] == "deny"
```

- [ ] **Step 2: Run, verify fails**

```bash
pytest tests/test_listener.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `src/claude_tap/listener.py`**

```python
"""DecisionListener: bind decision.sock and answer PermissionRequests."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from .config import decision_sock_path


@dataclass(frozen=True)
class DecisionRequest:
    request_id: str
    session_id: str
    tool_name: str
    tool_input: dict[str, Any]
    permission_suggestions: list = field(default_factory=list)


class DecisionListener:
    """Async server bound to decision.sock.

    Usage:
        async with DecisionListener() as listener:
            async for req in listener:
                decision = await your_logic(req)
                await listener.respond(req.request_id, decision)
    """

    def __init__(self, path: Optional[Path] = None):
        self._path = path or decision_sock_path()
        self._server: Optional[asyncio.Server] = None
        self._queue: Optional[asyncio.Queue[DecisionRequest]] = None
        self._writers: dict[str, asyncio.StreamWriter] = {}

    @property
    def path(self) -> Path:
        return self._path

    async def __aenter__(self) -> "DecisionListener":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            self._path.unlink()
        self._queue = asyncio.Queue()
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self._path),
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        # Close any orphan writers
        for w in list(self._writers.values()):
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass
        self._writers.clear()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            line = await reader.readline()
            if not line:
                writer.close()
                await writer.wait_closed()
                return
            payload = json.loads(line.decode("utf-8"))
            req = DecisionRequest(
                request_id=payload.get("request_id", ""),
                session_id=payload.get("session_id", ""),
                tool_name=payload.get("tool_name", ""),
                tool_input=payload.get("tool_input", {}),
                permission_suggestions=payload.get("permission_suggestions", []),
            )
            self._writers[req.request_id] = writer
            assert self._queue is not None
            await self._queue.put(req)
            # Keep writer open until respond() closes it.
        except (json.JSONDecodeError, ConnectionError):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def __aiter__(self) -> AsyncIterator[DecisionRequest]:
        assert self._queue is not None
        while True:
            req = await self._queue.get()
            yield req

    async def respond(self, request_id: str, decision: dict[str, Any]) -> None:
        """Send a decision response for a pending request."""
        writer = self._writers.pop(request_id, None)
        if writer is None:
            return
        try:
            response = {"request_id": request_id, "decision": decision}
            writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/test_listener.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/claude_tap/listener.py tests/test_listener.py
git commit -m "feat(listener): DecisionListener async server"
```

---

## Task 9: Wrapper renderer

**Files:**
- Create: `src/claude_tap/wrapper.py`
- Create: `tests/test_wrapper.py`

- [ ] **Step 1: Write the failing test**

`tests/test_wrapper.py`:

```python
import json
import shutil
import subprocess

import pytest

from claude_tap.wrapper import build_settings_json, render_wrapper


def test_settings_has_all_eight_hooks():
    s = build_settings_json()
    hooks = s["hooks"]
    assert set(hooks.keys()) == {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Notification",
        "Stop",
        "SessionEnd",
        "PermissionRequest",
    }


def test_each_hook_has_command_and_timeout():
    s = build_settings_json()
    for event_name, group in s["hooks"].items():
        assert isinstance(group, list) and len(group) == 1
        entry = group[0]
        assert entry["matcher"] == ""
        inner = entry["hooks"][0]
        assert inner["type"] == "command"
        assert inner["command"] == f"claude-tap-hook {event_name}"
        assert isinstance(inner["timeout"], int)


def test_permission_request_timeout_is_125():
    s = build_settings_json()
    assert s["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"] == 125


def test_session_end_timeout_short():
    s = build_settings_json()
    assert s["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"] == 2


def test_render_wrapper_starts_with_shebang():
    body = render_wrapper()
    assert body.startswith("#!/usr/bin/env bash\n")


def test_render_wrapper_contains_settings_json():
    body = render_wrapper()
    assert "claude-tap-hook PermissionRequest" in body
    # The settings JSON should have been substituted in (no placeholder)
    assert "__HOOKS_JSON__" not in body


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_render_wrapper_is_syntactically_valid_bash(tmp_path):
    """`bash -n` parse-checks without executing."""
    body = render_wrapper()
    f = tmp_path / "claude"
    f.write_text(body)
    result = subprocess.run(
        ["bash", "-n", str(f)], capture_output=True, text=True
    )
    assert result.returncode == 0, f"bash -n failed: {result.stderr}"
```

- [ ] **Step 2: Run, verify fails**

```bash
pytest tests/test_wrapper.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `src/claude_tap/wrapper.py`**

```python
"""Render the bash wrapper script that invokes real claude with hooks."""
from __future__ import annotations

import json

WRAPPER_TEMPLATE = r"""#!/usr/bin/env bash
# claude-tap wrapper — injects hooks when CLAUDE_TAP_ACTIVE=1.
# DO NOT EDIT — regenerated by `claude-tap install`.

set -e

# 1. Find real claude (skip self).
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
find_real_claude() {
    local IFS=:
    for d in $PATH; do
        [[ "$d" == "$SELF_DIR" ]] && continue
        [[ -x "$d/claude" ]] && printf '%s' "$d/claude" && return 0
    done
    return 1
}

REAL_CLAUDE="$(find_real_claude)" || { echo "claude not found in PATH" >&2; exit 127; }

# 2. Passthrough conditions.
if [[ "${CLAUDE_TAP_ACTIVE:-}" != "1" ]] || [[ "${CLAUDE_TAP_DISABLED:-}" == "1" ]]; then
    exec "$REAL_CLAUDE" "$@"
fi

# 3. Pass through subcommands that don't accept --session-id / --settings.
case "${1:-}" in
    mcp|config|api-key|--version|-v|--help|-h)
        exec "$REAL_CLAUDE" "$@"
        ;;
esac

# 4. Tmux detection.
if [[ -n "${TMUX_PANE:-}" ]] && command -v tmux >/dev/null 2>&1; then
    INFO=$(tmux display-message -t "$TMUX_PANE" -p '#{session_name}|#{window_id}' 2>/dev/null || true)
    if [[ -n "$INFO" ]]; then
        export CLAUDE_TAP_TMUX_SESSION_NAME="${INFO%%|*}"
        export CLAUDE_TAP_TMUX_WINDOW_ID="${INFO#*|}"
        export CLAUDE_TAP_TMUX_PANE_ID="$TMUX_PANE"
    fi
fi

# 5. Skip --session-id injection if user already passed one.
SKIP_SESSION_ID=false
for arg in "$@"; do
    case "$arg" in
        --resume|--resume=*|-r|--session-id|--session-id=*|--continue|-c)
            SKIP_SESSION_ID=true
            break
            ;;
    esac
done

# 6. Settings JSON with all 8 hooks (rendered at install time).
HOOKS_JSON='__HOOKS_JSON__'

# 7. Exec real claude with injected flags.
if $SKIP_SESSION_ID; then
    exec "$REAL_CLAUDE" --settings "$HOOKS_JSON" "$@"
elif command -v uuidgen >/dev/null 2>&1; then
    SID=$(uuidgen | tr '[:upper:]' '[:lower:]')
    exec "$REAL_CLAUDE" --session-id "$SID" --settings "$HOOKS_JSON" "$@"
else
    # uuidgen unavailable → skip session-id injection but keep settings.
    exec "$REAL_CLAUDE" --settings "$HOOKS_JSON" "$@"
fi
"""

# Per-event timeouts. PermissionRequest is sync, so its timeout must be
# slightly larger than CLAUDE_TAP_DECISION_TIMEOUT (default 120) so that
# claude-tap times out cleanly before claude SIGKILLs the hook.
_TIMEOUTS = {
    "SessionStart": 10,
    "UserPromptSubmit": 10,
    "PreToolUse": 10,
    "PostToolUse": 10,
    "Notification": 10,
    "Stop": 10,
    "SessionEnd": 2,
    "PermissionRequest": 125,
}


def build_settings_json() -> dict:
    """Build the --settings JSON payload registering all 8 hooks."""

    def hook_entry(event: str) -> list[dict]:
        return [{
            "matcher": "",
            "hooks": [{
                "type": "command",
                "command": f"claude-tap-hook {event}",
                "timeout": _TIMEOUTS[event],
            }],
        }]

    return {
        "hooks": {event: hook_entry(event) for event in _TIMEOUTS},
    }


def render_wrapper() -> str:
    """Render the bash wrapper with the settings JSON inlined."""
    settings = json.dumps(
        build_settings_json(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # Escape single quotes to keep it valid inside the bash single-quoted
    # string ('...').
    settings_escaped = settings.replace("'", "'\\''")
    return WRAPPER_TEMPLATE.replace("__HOOKS_JSON__", settings_escaped)
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/test_wrapper.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/claude_tap/wrapper.py tests/test_wrapper.py
git commit -m "feat(wrapper): bash wrapper template + settings JSON builder"
```

---

## Task 10: CLI — install / uninstall / watch / bridge / version

**Files:**
- Create: `src/claude_tap/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:

```python
import json
import os
import socket
import subprocess
import sys
import threading

import pytest

from claude_tap.cli import build_parser, cmd_install, cmd_uninstall, cmd_version
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
        import time
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
    assert response["decision"]["hookSpecificOutput"]["permissionDecision"] == "allow"
```

- [ ] **Step 2: Run, verify fails**

```bash
pytest tests/test_cli.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `src/claude_tap/cli.py`**

```python
"""claude-tap user-facing CLI."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from . import __version__
from .config import wrapper_path
from .listener import DecisionListener
from .stream import EventStream
from .wrapper import render_wrapper


def cmd_install(args) -> int:
    path = wrapper_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_wrapper())
    path.chmod(0o755)
    print(f"Wrote wrapper to {path}")
    return 0


def cmd_uninstall(args) -> int:
    path = wrapper_path()
    if path.exists():
        path.unlink()
        print(f"Removed {path}")
    parent = path.parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
    return 0


def _pretty(event: dict) -> str:
    et = event.get("event_type", "?")
    sid = event.get("claude", {}).get("session_id", "")[:8]
    tmux = event.get("tmux") or {}
    win = tmux.get("window_id", "-")
    payload = event.get("payload", {})
    if et == "user_prompt_submit":
        summary = payload.get("prompt", "")[:60]
    elif et in ("pre_tool_use", "post_tool_use"):
        ti = payload.get("tool_input", {})
        ti_str = json.dumps(ti, ensure_ascii=False)[:50]
        summary = f"{payload.get('tool_name')}({ti_str})"
    elif et == "permission_request":
        summary = f"⚠️  {payload.get('tool_name')} needs decision"
    else:
        summary = ""
    return f"[{et:<22}] sess={sid} win={win:>4} {summary}"


async def _watch_async(args) -> int:
    async for event in EventStream(from_start=False):
        if args.json:
            print(json.dumps(event, ensure_ascii=False))
        else:
            print(_pretty(event))
    return 0


def cmd_watch(args) -> int:
    try:
        return asyncio.run(_watch_async(args))
    except KeyboardInterrupt:
        return 0


def _build_decision(verb: str | None) -> dict[str, Any]:
    if verb == "allow":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "permissionDecision": "allow",
            }
        }
    if verb == "deny":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "permissionDecision": "deny",
                "permissionDecisionReason": "denied via claude-tap bridge",
            }
        }
    return {}


async def _bridge_stdio_async(args) -> int:
    async with DecisionListener() as listener:
        print(f"[bridge] listening on {listener.path}", file=sys.stderr)
        async for req in listener:
            print(f"\n>>> {req.tool_name}", flush=True)
            print(f"    request_id = {req.request_id}", flush=True)
            print(f"    session_id = {req.session_id}", flush=True)
            print(f"    tool_input = {json.dumps(req.tool_input, ensure_ascii=False)}", flush=True)
            print(f"    [allow / deny / <empty for {{}}]: ", end="", flush=True)
            answer = (await asyncio.to_thread(sys.stdin.readline)).strip().lower()
            decision = _build_decision(answer if answer in {"allow", "deny"} else None)
            await listener.respond(req.request_id, decision)
    return 0


async def _bridge_auto_async(args) -> int:
    decision = _build_decision(args.auto)
    async with DecisionListener() as listener:
        print(f"[bridge] auto={args.auto} listening on {listener.path}", file=sys.stderr)
        async for req in listener:
            await listener.respond(req.request_id, decision)
    return 0


def cmd_bridge(args) -> int:
    try:
        if args.auto:
            return asyncio.run(_bridge_auto_async(args))
        return asyncio.run(_bridge_stdio_async(args))
    except KeyboardInterrupt:
        return 0


def cmd_version(args) -> int:
    print(__version__)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claude-tap")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("install", help="Install ~/.claude-tap/bin/claude wrapper").set_defaults(fn=cmd_install)
    sub.add_parser("uninstall", help="Remove the wrapper").set_defaults(fn=cmd_uninstall)

    p_watch = sub.add_parser("watch", help="Subscribe to events.jsonl")
    p_watch.add_argument("--json", action="store_true", help="Print raw JSONL")
    p_watch.set_defaults(fn=cmd_watch)

    p_bridge = sub.add_parser("bridge", help="Bind decision.sock and answer PermissionRequests")
    p_bridge.add_argument("--stdio", action="store_true", help="Manual decisions via stdin (default)")
    p_bridge.add_argument("--auto", choices=["allow", "deny"], help="Auto-decide (testing)")
    p_bridge.set_defaults(fn=cmd_bridge)

    sub.add_parser("version", help="Print package version").set_defaults(fn=cmd_version)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "fn"):
        parser.print_help()
        return 2
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/test_cli.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/claude_tap/cli.py tests/test_cli.py
git commit -m "feat(cli): install/uninstall/watch/bridge/version"
```

---

## Task 11: Public API exports + sample consumer

**Files:**
- Modify: `src/claude_tap/__init__.py`
- Create: `examples/sample_consumer.py`
- Create: `tests/test_public_api.py`

- [ ] **Step 1: Write the failing test**

`tests/test_public_api.py`:

```python
def test_top_level_imports():
    import claude_tap
    # Public surface
    assert hasattr(claude_tap, "EventStream")
    assert hasattr(claude_tap, "DecisionListener")
    assert hasattr(claude_tap, "DecisionRequest")
    assert hasattr(claude_tap, "Event")
    assert hasattr(claude_tap, "ClaudeInfo")
    assert hasattr(claude_tap, "TmuxInfo")
    assert hasattr(claude_tap, "SCHEMA_VERSION")
    assert hasattr(claude_tap, "__version__")


def test_sample_consumer_compiles():
    """The reference example must at least syntactically parse."""
    import py_compile
    from pathlib import Path
    sample = Path(__file__).parent.parent / "examples" / "sample_consumer.py"
    py_compile.compile(str(sample), doraise=True)
```

- [ ] **Step 2: Run, verify fails**

```bash
pytest tests/test_public_api.py -v
```

Expected: AttributeError on EventStream, etc.

- [ ] **Step 3: Update `src/claude_tap/__init__.py`**

```python
"""claude-tap: Claude Code → structured events + decision bridge."""

from ._version import __version__
from .events import SCHEMA_VERSION, ClaudeInfo, Event, TmuxInfo
from .listener import DecisionListener, DecisionRequest
from .stream import EventStream

__all__ = [
    "__version__",
    "SCHEMA_VERSION",
    "Event",
    "ClaudeInfo",
    "TmuxInfo",
    "EventStream",
    "DecisionListener",
    "DecisionRequest",
]
```

- [ ] **Step 4: Write `examples/sample_consumer.py`** — copy verbatim from spec

```python
#!/usr/bin/env python3
"""Reference consumer for claude-tap.

Subscribes to events (via EventStream — looks like push, no manual
tail) and binds decision.sock to answer PermissionRequests
interactively from stdin.

Copy this file as a starting point for your own consumer (chat
backend, GUI, ...).
"""

import asyncio
import json
import sys

from claude_tap import DecisionListener, EventStream


async def watch_events() -> None:
    """Print every event as it arrives.

    EventStream is an async iterator. v0.1 polls events.jsonl at 100 ms
    intervals; latency is bounded by that interval. v0.2 may switch to
    inotify-backed push. From the consumer's perspective, it is always
    just `async for`.
    """
    async for event in EventStream():
        et = event["event_type"]
        sid = event["claude"]["session_id"][:8]
        tmux = event.get("tmux") or {}
        win = tmux.get("window_id", "-")
        payload = event.get("payload", {})

        if et == "user_prompt_submit":
            summary = payload.get("prompt", "")[:60]
        elif et in ("pre_tool_use", "post_tool_use"):
            summary = (
                f'{payload.get("tool_name")}'
                f'({json.dumps(payload.get("tool_input", {}), ensure_ascii=False)[:50]})'
            )
        elif et == "permission_request":
            summary = f'⚠️  {payload.get("tool_name")} needs decision'
        else:
            summary = ""

        print(f"[{et:<22}] sess={sid} win={win:>4} {summary}")


async def serve_decisions() -> None:
    """For each PermissionRequest, prompt operator via stdin.

    A real consumer would route to a chat platform / a GUI / an LLM
    judge instead of blocking on stdin.
    """
    async with DecisionListener() as listener:
        async for req in listener:
            print("\n>>> Decision needed")
            print(f"    session   = {req.session_id}")
            print(f"    tool      = {req.tool_name}")
            print(
                f"    input     = "
                f"{json.dumps(req.tool_input, ensure_ascii=False, indent=2)}"
            )
            for s in req.permission_suggestions or []:
                print(f"    suggestion= {s}")
            print(
                "    type 'allow' or 'deny' (default deny): ",
                end="",
                flush=True,
            )

            answer = (await asyncio.to_thread(sys.stdin.readline)).strip().lower()
            allow = answer == "allow"

            decision = {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "permissionDecision": "allow" if allow else "deny",
                    **(
                        {"permissionDecisionReason": "denied via sample consumer"}
                        if not allow
                        else {}
                    ),
                }
            }
            await listener.respond(req.request_id, decision)
            print(
                f"<<< sent: {decision['hookSpecificOutput']['permissionDecision']}\n"
            )


async def main() -> None:
    await asyncio.gather(watch_events(), serve_decisions())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
```

- [ ] **Step 5: Run tests, verify pass**

```bash
pytest tests/test_public_api.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/claude_tap/__init__.py examples/sample_consumer.py tests/test_public_api.py
git commit -m "feat(api): public exports + sample_consumer.py reference"
```

---

## Task 12: End-to-end integration test

**Files:**
- Create: `tests/test_integration.py`

This test spawns the real `claude-tap-hook` as a subprocess and runs a
real `DecisionListener` in the same process — exercising the wire
through process boundaries.

- [ ] **Step 1: Write the test**

`tests/test_integration.py`:

```python
"""End-to-end: claude-tap-hook subprocess ↔ DecisionListener server."""
import asyncio
import json
import os
import sys
from pathlib import Path

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
    payload = json.dumps({
        "session_id": "abc",
        "transcript_path": "/tmp/t.jsonl",
        "cwd": "/tmp",
        "permission_mode": "default",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    })

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "claude_tap.hook", "PermissionRequest",
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

    # events.jsonl should have one line
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
    payload = json.dumps({
        "session_id": "abc",
        "transcript_path": "/tmp/t.jsonl",
        "cwd": "/tmp",
        "permission_mode": "default",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    })

    async with DecisionListener() as listener:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "claude_tap.hook", "PermissionRequest",
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
                {"hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "permissionDecision": "allow",
                }},
            )
            break

        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        assert proc.returncode == 0, f"stderr: {stderr.decode()}"
        decision = json.loads(stdout.decode())
        assert decision["hookSpecificOutput"]["permissionDecision"] == "allow"


@pytest.mark.asyncio
async def test_hook_subprocess_concurrent_requests(isolated_tap_dir):
    """Two hooks fire concurrently; listener routes both correctly."""
    env = {
        **os.environ,
        "CLAUDE_TAP_DIR": str(isolated_tap_dir),
        "CLAUDE_TAP_DECISION_TIMEOUT": "5.0",
    }

    def make_payload(session_id: str, command: str) -> str:
        return json.dumps({
            "session_id": session_id,
            "transcript_path": "/tmp/t.jsonl",
            "cwd": "/tmp",
            "permission_mode": "default",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        })

    async with DecisionListener() as listener:
        proc1 = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "claude_tap.hook", "PermissionRequest",
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        proc2 = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "claude_tap.hook", "PermissionRequest",
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
                {"hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "permissionDecision": d,
                }},
            )
            seen += 1
            if seen >= 2:
                break

        out1, _ = await asyncio.wait_for(proc1.communicate(), timeout=5.0)
        out2, _ = await asyncio.wait_for(proc2.communicate(), timeout=5.0)
        d1 = json.loads(out1.decode())
        d2 = json.loads(out2.decode())
        assert d1["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert d2["hookSpecificOutput"]["permissionDecision"] == "deny"


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
            sys.executable, "-m", "claude_tap.hook", event_name,
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
```

- [ ] **Step 2: Run integration tests**

```bash
pytest tests/test_integration.py -v
```

Expected: 4 passed.

- [ ] **Step 3: Run full test suite**

```bash
pytest -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test(integration): subprocess hook ↔ listener end-to-end"
```

---

## Task 13: README polish + spec sync check

**Files:**
- Modify: `README.md`
- Possibly modify: `docs/superpowers/specs/2026-05-08-claude-tap-design.md`

- [ ] **Step 1: Replace `README.md` with proper user-facing intro**

```markdown
# claude-tap

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

Wrap Claude Code: structured event stream + synchronous decision bridge.

`claude-tap` installs a thin shell wrapper in front of `claude` (gated
by `CLAUDE_TAP_ACTIVE=1` so it is harmless when not opted in). When
active, it injects all eight Claude Code hooks
(`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
`Notification`, `Stop`, `SessionEnd`, `PermissionRequest`) and routes
them into:

- **`~/.claude-tap/events.jsonl`** — append-only event stream (durable,
  always written, single line per event).
- **`~/.claude-tap/decision.sock`** — optional unix socket your consumer
  binds when it wants to answer `PermissionRequest` synchronously.

Designed for any consumer that needs to know what a Claude Code session
is doing — chat backends, IDE integrations, terminal multiplexer
dashboards. claude-tap has no opinion about who consumes the stream.

## Status

v0.1 alpha.

## Install

```bash
uv tool install git+https://github.com/wuwenrui555/claude-tap.git
claude-tap install                 # writes ~/.claude-tap/bin/claude
```

## Use

```bash
# In one terminal: subscribe to events
claude-tap watch

# In another: bind the decision socket and answer interactively
claude-tap bridge --stdio

# In a third: drive Claude through the wrapper
CLAUDE_TAP_ACTIVE=1 ~/.claude-tap/bin/claude
```

Or use the Python API directly:

```python
from claude_tap import EventStream, DecisionListener

async for event in EventStream():
    print(event["event_type"], event["claude"]["session_id"])

async with DecisionListener() as listener:
    async for req in listener:
        await listener.respond(req.request_id, decision)
```

See `examples/sample_consumer.py` for a complete reference consumer.

## Design

Full design at [`docs/superpowers/specs/2026-05-08-claude-tap-design.md`](docs/superpowers/specs/2026-05-08-claude-tap-design.md).
```

- [ ] **Step 2: Verify spec / impl consistency**

Re-read `docs/superpowers/specs/2026-05-08-claude-tap-design.md` once
through. Verify:

- All 8 hook events match what's registered in `wrapper.py:_TIMEOUTS`
- Failure modes table matches what's actually implemented in `socket_proto.py:try_socket_decision`
- "Inotify" wording was updated to "polling" (already done in Task 7)
- `claude-tap` and `claude-tap-hook` are both listed in CLI surface

If any drift remains, fix inline. If implementation deviates from spec
in a meaningful way, update the spec; do not silently let them
disagree.

- [ ] **Step 3: Final test run + commit**

```bash
pytest -v
ruff check src/ tests/
ruff format --check src/ tests/
```

Expected: all clean.

```bash
git add README.md docs/
git commit -m "docs: README polish + spec/impl consistency check"
```

---

## Self-review checklist

Before declaring complete, verify against the spec:

1. **Spec coverage**: every component in spec's "Components" section has an
   implementation file. ✓ (wrapper, hook, events, socket_proto, stream,
   listener, CLI all present.)
2. **Failure modes**: every row in the spec's "Failure modes" table is
   covered by tests in `test_socket_proto.py` or `test_hook.py`. Walk the
   table once and confirm.
3. **Hook return value semantics**: the table in the spec says `{}`
   means "no opinion → TUI fallback". The implementation returns `"{}"`
   string from `hook.run()` whenever the socket fails — verify with
   `test_hook.py::test_run_permission_request_no_listener`.
4. **8 events**: spec lists 8 hook events. `wrapper.py:_TIMEOUTS` has 8
   entries. `hook.py:_EVENT_NAME_MAP` has 8 entries. Cross-check.
5. **Schema version**: spec says `schema_version: 1`. `events.py:SCHEMA_VERSION = 1`. ✓
6. **Tmux fields**: spec says session_name, window_id, pane_id (3
   fields). `events.py:TmuxInfo` has those exact 3.

If a gap is found, add a follow-up task and commit before transitioning
to user review.
