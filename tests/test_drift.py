import json

import pytest

from claude_tap import drift


@pytest.fixture(autouse=True)
def clear_dedup():
    drift.reset_dedup()
    yield
    drift.reset_dedup()


def _drift_log_lines(d):
    p = d / "drift.log"
    if not p.exists():
        return []
    return [line for line in p.read_text().splitlines() if line.strip()]


def test_known_events_lists_eight():
    assert len(drift.known_events()) == 8
    assert "PermissionRequest" in drift.known_events()
    assert "SessionStart" in drift.known_events()


def test_expected_fields_unknown_event_returns_empty():
    req, opt = drift.expected_fields("Bogus")
    assert req == frozenset()
    assert opt == frozenset()


def test_clean_payload_logs_nothing(isolated_tap_dir):
    raw = {
        "session_id": "s",
        "transcript_path": "/t.jsonl",
        "cwd": "/c",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "permission_mode": "default",
    }
    drift.check("PreToolUse", raw)
    assert _drift_log_lines(isolated_tap_dir) == []


def test_missing_required_field_logged(isolated_tap_dir):
    raw = {
        "transcript_path": "/t.jsonl",
        "cwd": "/c",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        # session_id missing!
    }
    drift.check("PreToolUse", raw)
    lines = _drift_log_lines(isolated_tap_dir)
    assert len(lines) == 1
    assert "PreToolUse" in lines[0]
    assert "MISSING" in lines[0]
    assert "session_id" in lines[0]


def test_multiple_missing_fields_one_line_each(isolated_tap_dir):
    raw = {
        "hook_event_name": "PreToolUse",
        # session_id, transcript_path, cwd, tool_name, tool_input all missing
    }
    drift.check("PreToolUse", raw)
    lines = _drift_log_lines(isolated_tap_dir)
    # 5 missing fields × 1 line each
    assert len(lines) == 5
    for missing in ("session_id", "transcript_path", "cwd", "tool_name", "tool_input"):
        assert any(f"MISSING | {missing}" in line for line in lines), missing


def test_unknown_top_level_field_logged(isolated_tap_dir):
    raw = {
        "session_id": "s",
        "transcript_path": "/t.jsonl",
        "cwd": "/c",
        "hook_event_name": "SessionStart",
        "permission_mode": "default",
        "brand_new_field": "...",
    }
    drift.check("SessionStart", raw)
    lines = _drift_log_lines(isolated_tap_dir)
    assert len(lines) == 1
    assert "SessionStart" in lines[0]
    assert "UNKNOWN" in lines[0]
    assert "brand_new_field" in lines[0]


def test_unknown_event_name_logged(isolated_tap_dir):
    raw = {"session_id": "s"}
    drift.check("FuturisticEvent", raw)
    lines = _drift_log_lines(isolated_tap_dir)
    assert len(lines) == 1
    assert "UNKNOWN_EVENT" in lines[0]
    assert "FuturisticEvent" in lines[0]


def test_dedup_same_anomaly_only_logged_once(isolated_tap_dir):
    raw_with_unknown = {
        "session_id": "s",
        "transcript_path": "/t.jsonl",
        "cwd": "/c",
        "hook_event_name": "SessionStart",
        "novel_field": "x",
    }
    drift.check("SessionStart", raw_with_unknown)
    drift.check("SessionStart", raw_with_unknown)
    drift.check("SessionStart", raw_with_unknown)
    lines = _drift_log_lines(isolated_tap_dir)
    assert len(lines) == 1


def test_distinct_anomalies_get_distinct_lines(isolated_tap_dir):
    drift.check(
        "SessionStart",
        {
            "session_id": "s",
            "transcript_path": "/t.jsonl",
            "cwd": "/c",
            "hook_event_name": "SessionStart",
            "novel_a": "x",
        },
    )
    drift.check(
        "SessionStart",
        {
            "session_id": "s",
            "transcript_path": "/t.jsonl",
            "cwd": "/c",
            "hook_event_name": "SessionStart",
            "novel_b": "y",
        },
    )
    lines = _drift_log_lines(isolated_tap_dir)
    assert len(lines) == 2


def test_check_never_raises(isolated_tap_dir):
    # Pass garbage that doesn't even look like dict items.
    drift.check("PreToolUse", {})  # all required missing
    drift.check("UnknownEvent", {})
    # If we got here without exception, fine.


def test_real_2026_05_08_payload_no_drift(isolated_tap_dir):
    """The empirical PermissionRequest payload we observed must not
    trigger any drift entries (it's the schema we encoded)."""
    raw_text = (
        '{"session_id":"f1baf094-6834-46da-ba25-fdfa9cd4a73c",'
        '"transcript_path":"/x/t.jsonl",'
        '"cwd":"/x",'
        '"permission_mode":"default",'
        '"effort":{"level":"xhigh"},'
        '"hook_event_name":"PermissionRequest",'
        '"tool_name":"Bash",'
        '"tool_input":{"command":"touch /tmp/x"},'
        '"permission_suggestions":[{"type":"addDirectories","directories":["/tmp"]}]}'
    )
    raw = json.loads(raw_text)
    drift.check("PermissionRequest", raw)
    assert _drift_log_lines(isolated_tap_dir) == []


def test_session_end_uses_reason(isolated_tap_dir):
    """SessionEnd's required field is `reason` (empirical, 2026-05-08).

    Note: the official docs claim `end_reason`, but real Claude Code
    2.1.136 sends `reason`. Drift detection caught this on first live
    run; we follow reality, not docs.
    """
    raw = {
        "session_id": "s",
        "transcript_path": "/t.jsonl",
        "cwd": "/c",
        "hook_event_name": "SessionEnd",
        "reason": "logout",
    }
    drift.check("SessionEnd", raw)
    assert _drift_log_lines(isolated_tap_dir) == []


def test_session_end_with_docs_field_flags_drift(isolated_tap_dir):
    """If Claude ever adopts the documented `end_reason`, drift should
    flag both MISSING reason and UNKNOWN end_reason — that's our
    signal to update the schema to match new reality."""
    raw = {
        "session_id": "s",
        "transcript_path": "/t.jsonl",
        "cwd": "/c",
        "hook_event_name": "SessionEnd",
        "end_reason": "logout",  # the field name docs claim
    }
    drift.check("SessionEnd", raw)
    lines = _drift_log_lines(isolated_tap_dir)
    assert any("MISSING | reason" in line for line in lines), lines
    assert any("UNKNOWN | end_reason" in line for line in lines), lines


def test_dedup_persists_across_simulated_process_restart(isolated_tap_dir):
    """A fresh process must read drift.log and skip already-logged keys.

    Simulates the real production case: claude-tap-hook is invoked as
    a fresh subprocess for every Claude hook fire. Without persistence,
    drift.log would grow by one line per (anomaly × hook fire), which
    quickly drowns the signal.
    """
    raw = {
        "session_id": "s",
        "transcript_path": "/t.jsonl",
        "cwd": "/c",
        "hook_event_name": "SessionStart",
        "future_field": "x",
    }
    drift.check("SessionStart", raw)
    assert len(_drift_log_lines(isolated_tap_dir)) == 1

    # Simulate brand-new process: in-memory cache cleared, will reload
    # from drift.log on next check.
    drift.reset_dedup(reload_from_disk=True)

    drift.check("SessionStart", raw)
    drift.check("SessionStart", raw)
    # Still exactly one line — the persistent dedup caught both retries.
    assert len(_drift_log_lines(isolated_tap_dir)) == 1


def test_drift_log_path_public_accessor():
    """Public accessor must expose the canonical path."""
    p = drift.drift_log_path()
    assert p.name == "drift.log"


def test_notification_required_fields(isolated_tap_dir):
    """Notification requires `message` (empirical, 2026-05-08).

    Same docs/reality mismatch as SessionEnd: docs say
    `notification_type` + `notification_message`, real Claude Code
    sends only `message`. Drift caught it on first run.
    """
    raw = {
        "session_id": "s",
        "transcript_path": "/t.jsonl",
        "cwd": "/c",
        "hook_event_name": "Notification",
        "message": "Claude needs input",
    }
    drift.check("Notification", raw)
    assert _drift_log_lines(isolated_tap_dir) == []
