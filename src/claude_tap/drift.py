"""Hook payload schema drift detection.

Validates Claude Code's hook stdin payload against the schema we
observed at the time of writing (Claude Code 2.1.133, 2026-05-08) and
logs anomalies to ``$CLAUDE_TAP_DIR/drift.log``. Two kinds:

- **MISSING**: a required field we depend on is absent. Likely means
  Claude renamed or dropped it; downstream extraction may produce
  empty values until we update the schema.
- **UNKNOWN**: a top-level field not in our optional set is present.
  Claude probably added something new. Informational; downstream
  behavior is fine, but may be worth surfacing in events.

Each unique anomaly (event_type, kind, field) is logged once per
process to keep ``drift.log`` small. Drift checking is **best-effort**:
any error inside this module is swallowed so the hook still serves
Claude.

Output line format:

    2026-05-08T13:00:00+00:00 | <event> | <KIND> | <field> | seen=<count>

Run ``grep MISSING ~/.claude-tap/drift.log`` to find new schema
problems after a Claude Code release.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import claude_tap_dir

# Minimal schema of what each Claude Code hook stdin event carries.
# Maintain by hand. When drift.log shows new MISSING/UNKNOWN entries,
# update here and the relevant extraction in `hook.py`.
#
# `required` = fields whose absence breaks our extraction (we depend
#              on them to populate the event envelope or payload).
# `optional` = fields we know about but tolerate not seeing. Anything
#              outside the union of these two sets logs as UNKNOWN.

# Schemas verified empirically against Claude Code 2.1.136 on 2026-05-08.
# Source: actual hook stdin captured during a live session (drift.log
# round-trip). Where this contradicts the official docs at
# code.claude.com/docs/en/hooks (e.g., docs claim SessionEnd uses
# `end_reason` and Notification uses `notification_message`/
# `notification_type`), the empirical observation wins. See
# docs/verifying-hook-contract.md for the maintenance loop.

_COMMON_REQUIRED = {"session_id", "transcript_path", "cwd", "hook_event_name"}
_COMMON_OPTIONAL = {"permission_mode", "effort"}

# Tool-related hooks consistently include tool_use_id; PostToolUse also
# includes duration_ms. Both are observed but we don't extract them.
_TOOL_OPTIONAL = {"tool_use_id"}

_EXPECTED: dict[str, dict[str, set[str]]] = {
    "SessionStart": {
        "required": _COMMON_REQUIRED,
        "optional": _COMMON_OPTIONAL | {"source"},
    },
    "UserPromptSubmit": {
        "required": _COMMON_REQUIRED | {"prompt"},
        "optional": _COMMON_OPTIONAL,
    },
    "PreToolUse": {
        "required": _COMMON_REQUIRED | {"tool_name", "tool_input"},
        "optional": _COMMON_OPTIONAL | _TOOL_OPTIONAL,
    },
    "PostToolUse": {
        "required": _COMMON_REQUIRED | {"tool_name", "tool_input", "tool_response"},
        "optional": _COMMON_OPTIONAL | _TOOL_OPTIONAL | {"duration_ms"},
    },
    "Notification": {
        # Empirical: real stdin contains `message`, not `notification_message`.
        "required": _COMMON_REQUIRED | {"message"},
        "optional": _COMMON_OPTIONAL,
    },
    "Stop": {
        "required": _COMMON_REQUIRED,
        "optional": _COMMON_OPTIONAL | {"stop_hook_active", "response"},
    },
    "SessionEnd": {
        # Empirical: real stdin contains `reason`, not `end_reason`.
        "required": _COMMON_REQUIRED | {"reason"},
        "optional": _COMMON_OPTIONAL,
    },
    "PermissionRequest": {
        "required": _COMMON_REQUIRED | {"tool_name", "tool_input"},
        "optional": _COMMON_OPTIONAL | _TOOL_OPTIONAL | {"permission_suggestions"},
    },
}


def known_events() -> frozenset[str]:
    """The set of Claude Code event names this module knows about."""
    return frozenset(_EXPECTED.keys())


def expected_fields(event_name: str) -> tuple[frozenset[str], frozenset[str]]:
    """Return (required, optional) field-name sets for an event.

    Returns (frozenset(), frozenset()) for unknown event_name.
    """
    schema = _EXPECTED.get(event_name)
    if schema is None:
        return frozenset(), frozenset()
    return frozenset(schema["required"]), frozenset(schema["optional"])


# Dedup table keyed by (event, kind, field). Initialized lazily from
# the existing drift.log on disk so the dedup is **persistent across
# hook subprocess invocations**: if Claude triggers a hook that
# re-fires the same drift, we don't keep appending duplicate lines.
#
# `None` means "not yet loaded"; once loaded, an empty dict means
# "nothing seen yet". `reset_dedup(reload_from_disk=True)` forces
# a fresh load (test helper).
_seen: dict[tuple[str, str, str], int] | None = None


def reset_dedup(reload_from_disk: bool = False) -> None:
    """Clear the in-memory dedup table.

    By default leaves it empty (next ``check`` call starts fresh and
    will not consult drift.log). Pass ``reload_from_disk=True`` to
    simulate a brand-new process: the next ``check`` call will
    re-populate from drift.log first.
    """
    global _seen
    _seen = None if reload_from_disk else {}


def drift_log_path() -> Path:
    """Public accessor for the drift log path.

    The path is derived from ``CLAUDE_TAP_DIR`` at call time so test
    fixtures (which monkeypatch the env var) work transparently.
    """
    return claude_tap_dir() / "drift.log"


# Backwards-compatible alias retained for existing imports.
_drift_log_path = drift_log_path


def _load_persistent_seen() -> dict[tuple[str, str, str], int]:
    """Parse drift.log into the dedup table.

    Each line has the form ``ts | event | KIND | field | seen=N``.
    Failures (file missing, malformed lines) yield an empty result —
    drift detection must never break the hook.
    """
    result: dict[tuple[str, str, str], int] = {}
    path = drift_log_path()
    try:
        if not path.exists():
            return result
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 4:
                continue
            event, kind, field = parts[1], parts[2], parts[3]
            result[(event, kind, field)] = 1
    except OSError:
        pass
    return result


def _ensure_seen() -> dict[tuple[str, str, str], int]:
    global _seen
    if _seen is None:
        _seen = _load_persistent_seen()
    return _seen


def _append_line(line: str) -> None:
    """Append one drift line atomically. Failures swallowed."""
    try:
        path = drift_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        pass


def _record(event_name: str, kind: str, field: str) -> None:
    """Increment dedup count and write one line on first sighting."""
    seen = _ensure_seen()
    key = (event_name, kind, field)
    count = seen.get(key, 0) + 1
    seen[key] = count
    if count > 1:
        return  # Already logged once (this process or a prior one).
    ts = datetime.now(UTC).isoformat()
    line = f"{ts} | {event_name} | {kind} | {field} | seen=1\n"
    _append_line(line)


def check(event_name: str, raw: dict[str, Any]) -> None:
    """Validate `raw` (Claude's hook stdin payload) against expected schema.

    Logs each unique (event_name, kind, field) anomaly once per process.
    Recognized anomalies:

    - ``UNKNOWN_EVENT``: Claude fired an event_name we don't have a
      schema for. We record the event_name itself (field column).
    - ``MISSING``: a required field is absent. Field column is the
      missing field name.
    - ``UNKNOWN``: a top-level key in `raw` is not in
      required ∪ optional. Field column is the new key.

    Best-effort: any internal error is swallowed.
    """
    try:
        if event_name not in _EXPECTED:
            _record(event_name, "UNKNOWN_EVENT", event_name)
            return

        required, optional = expected_fields(event_name)
        present = set(raw.keys())

        for missing in sorted(required - present):
            _record(event_name, "MISSING", missing)

        for unknown in sorted(present - required - optional):
            _record(event_name, "UNKNOWN", unknown)
    except Exception:
        # Drift detection must never break the hook.
        pass
