from pathlib import Path

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
