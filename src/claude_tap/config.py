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
