from pathlib import Path

from claude_tap import config as config_module
from claude_tap.config import (
    DEFAULT_DECISION_TIMEOUT,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POLL_MAX_DURATION,
    DEFAULT_PRETTY_WIDTH,
    _parse_settings_env,
    claude_tap_dir,
    decision_sock_path,
    decision_timeout,
    events_path,
    poll_interval,
    poll_max_duration,
    pretty_width,
    settings_env_path,
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


def test_poll_interval_defaults(isolated_tap_dir, monkeypatch):
    monkeypatch.delenv("CLAUDE_TAP_POLL_INTERVAL", raising=False)
    assert poll_interval() == DEFAULT_POLL_INTERVAL


def test_poll_interval_override(isolated_tap_dir, monkeypatch):
    monkeypatch.setenv("CLAUDE_TAP_POLL_INTERVAL", "0.05")
    assert poll_interval() == 0.05


def test_poll_max_duration_defaults(isolated_tap_dir, monkeypatch):
    monkeypatch.delenv("CLAUDE_TAP_POLL_MAX_DURATION", raising=False)
    assert poll_max_duration() == DEFAULT_POLL_MAX_DURATION


def test_poll_max_duration_override(isolated_tap_dir, monkeypatch):
    monkeypatch.setenv("CLAUDE_TAP_POLL_MAX_DURATION", "5")
    assert poll_max_duration() == 5.0


def test_pretty_width_default(isolated_tap_dir, monkeypatch):
    monkeypatch.delenv("CLAUDE_TAP_PRETTY_WIDTH", raising=False)
    assert pretty_width() == DEFAULT_PRETTY_WIDTH


def test_pretty_width_override(isolated_tap_dir, monkeypatch):
    monkeypatch.setenv("CLAUDE_TAP_PRETTY_WIDTH", "200")
    assert pretty_width() == 200


def test_pretty_width_invalid_falls_back(isolated_tap_dir, monkeypatch):
    monkeypatch.setenv("CLAUDE_TAP_PRETTY_WIDTH", "abc")
    assert pretty_width() == DEFAULT_PRETTY_WIDTH


def test_pretty_width_non_positive_falls_back(isolated_tap_dir, monkeypatch):
    monkeypatch.setenv("CLAUDE_TAP_PRETTY_WIDTH", "0")
    assert pretty_width() == DEFAULT_PRETTY_WIDTH


def test_settings_env_path(isolated_tap_dir):
    assert settings_env_path() == isolated_tap_dir / "settings.env"


def test_parse_settings_env_basic(tmp_path: Path):
    f = tmp_path / "settings.env"
    f.write_text(
        "# Comment line\n"
        "FOO=bar\n"
        "BAZ = qux \n"
        '\nQUOTED="value with spaces"\n'
        "SQUOTED='single'\n"
        "INLINE=value # trailing comment\n"
        "BAD line without equals\n"
    )
    parsed = _parse_settings_env(f)
    assert parsed["FOO"] == "bar"
    assert parsed["BAZ"] == "qux"
    assert parsed["QUOTED"] == "value with spaces"
    assert parsed["SQUOTED"] == "single"
    assert parsed["INLINE"] == "value"


def test_parse_settings_env_missing_returns_empty(tmp_path: Path):
    assert _parse_settings_env(tmp_path / "nope.env") == {}


def test_settings_env_loaded_into_environ(tmp_path: Path, monkeypatch):
    """Calling _load_settings_env_files() picks up file values via setdefault."""
    monkeypatch.setenv("CLAUDE_TAP_DIR", str(tmp_path))
    settings_file = tmp_path / "settings.env"
    settings_file.write_text("CLAUDE_TAP_POLL_INTERVAL=0.25\n")
    monkeypatch.delenv("CLAUDE_TAP_POLL_INTERVAL", raising=False)

    # Reset the loader-state guard so the loader runs again.
    config_module._LOADED_SETTINGS_FROM.clear()
    config_module._load_settings_env_files()

    assert poll_interval() == 0.25


def test_settings_env_does_not_override_explicit_env(tmp_path: Path, monkeypatch):
    """Shell-exported value wins over file value."""
    monkeypatch.setenv("CLAUDE_TAP_DIR", str(tmp_path))
    settings_file = tmp_path / "settings.env"
    settings_file.write_text("CLAUDE_TAP_POLL_INTERVAL=0.25\n")
    monkeypatch.setenv("CLAUDE_TAP_POLL_INTERVAL", "0.99")

    config_module._LOADED_SETTINGS_FROM.clear()
    config_module._load_settings_env_files()

    assert poll_interval() == 0.99
