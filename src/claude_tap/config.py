"""Environment variable resolution and path helpers.

claude-tap reads its configuration from environment variables. To
let users persist tunables without editing shell rc files, we also
look for a ``settings.env`` file in two places (loaded once at
import, with shell-exported values winning over file values):

1. ``settings.env`` in the current working directory (project-local)
2. ``$CLAUDE_TAP_DIR/settings.env`` (global, default
   ``~/.claude-tap/settings.env``)

File format is ``KEY=value`` per line, ``#`` starts a comment, and
the value can be wrapped in single or double quotes. The parser is
intentionally minimal — claude-tap has no runtime dependencies, so
we do not pull in ``python-dotenv``.

Recognized settings:

* ``CLAUDE_TAP_DIR`` — state directory (default ``~/.claude-tap``).
* ``CLAUDE_TAP_SURFACE_ID`` — opaque consumer identifier stamped on
  events (default empty).
* ``CLAUDE_TAP_DECISION_TIMEOUT`` — seconds to wait for a synchronous
  ``PermissionRequest`` decision before falling back to Claude's
  default (default 120).
* ``CLAUDE_TAP_POLL_INTERVAL`` — polling cadence (s) for the
  ``events.jsonl`` tail and the scoped post-tool-use mid-turn
  polling task in ``MessageStream`` (default 0.1).
* ``CLAUDE_TAP_POLL_MAX_DURATION`` — safety bound (s) on the scoped
  post-tool-use polling task; the task self-stops after this many
  seconds even if no next hook arrives (default 30).
* ``CLAUDE_TAP_PRETTY_WIDTH`` — visual cell width used by
  ``claude-tap watch-messages`` for both the emit-time separator
  line and the body trim cap (default 100). One knob for both:
  the block reads as a unit so the two stay synced.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_DIR = "~/.claude-tap"
DEFAULT_DECISION_TIMEOUT = 120.0
DEFAULT_POLL_INTERVAL = 0.1
DEFAULT_POLL_MAX_DURATION = 30.0
DEFAULT_PRETTY_WIDTH = 100

_SETTINGS_ENV_FILENAME = "settings.env"
_LOADED_SETTINGS_FROM: list[Path] = []
_KEY_VALUE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def claude_tap_dir() -> Path:
    raw = os.environ.get("CLAUDE_TAP_DIR", DEFAULT_DIR)
    return Path(raw).expanduser()


def events_path() -> Path:
    return claude_tap_dir() / "events.jsonl"


def decision_sock_path() -> Path:
    return claude_tap_dir() / "decision.sock"


def wrapper_path() -> Path:
    return claude_tap_dir() / "bin" / "claude"


def settings_env_path() -> Path:
    """Global settings.env path under ``$CLAUDE_TAP_DIR``."""
    return claude_tap_dir() / _SETTINGS_ENV_FILENAME


def surface_id() -> str:
    return os.environ.get("CLAUDE_TAP_SURFACE_ID", "")


def decision_timeout() -> float:
    return _float_env("CLAUDE_TAP_DECISION_TIMEOUT", DEFAULT_DECISION_TIMEOUT)


def poll_interval() -> float:
    return _float_env("CLAUDE_TAP_POLL_INTERVAL", DEFAULT_POLL_INTERVAL)


def poll_max_duration() -> float:
    return _float_env("CLAUDE_TAP_POLL_MAX_DURATION", DEFAULT_POLL_MAX_DURATION)


def pretty_width() -> int:
    """Visual cell width for both watch-messages separator and body trim cap."""
    raw = os.environ.get("CLAUDE_TAP_PRETTY_WIDTH", "")
    if not raw:
        return DEFAULT_PRETTY_WIDTH
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_PRETTY_WIDTH
    return value if value > 0 else DEFAULT_PRETTY_WIDTH


def loaded_settings_files() -> list[Path]:
    """Paths from which settings.env values were loaded at import time."""
    return list(_LOADED_SETTINGS_FROM)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_settings_env(path: Path) -> dict[str, str]:
    """Read a KEY=value file. Supports ``#`` comments and quoted values.

    Returns an empty dict if the file does not exist or cannot be
    read; never raises.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _KEY_VALUE_RE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        # Strip an unquoted inline comment.
        if value and not (value.startswith('"') or value.startswith("'")):
            if "#" in value:
                value = value.split("#", 1)[0].strip()
        # Strip matching surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        out[key] = value
    return out


def _load_settings_env_files() -> None:
    """Source ``settings.env`` into ``os.environ`` once at import.

    Loaded in priority order (later wins among files; shell exports
    always win because we use ``setdefault``):

    1. ``./settings.env`` (cwd, project-local)
    2. ``$CLAUDE_TAP_DIR/settings.env`` (global)
    """
    paths = [Path(_SETTINGS_ENV_FILENAME), settings_env_path()]
    for path in paths:
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        values = _parse_settings_env(path)
        if not values:
            continue
        for key, val in values.items():
            os.environ.setdefault(key, val)
        _LOADED_SETTINGS_FROM.append(path)


_load_settings_env_files()
