"""Shared pytest fixtures."""

import pytest


@pytest.fixture
def isolated_tap_dir(tmp_path, monkeypatch):
    """Override CLAUDE_TAP_DIR to a fresh tmp dir for the test.

    Also clears any tmux- or surface-related env vars that could leak in
    from the host shell and break test isolation.
    """
    monkeypatch.setenv("CLAUDE_TAP_DIR", str(tmp_path))
    for var in [
        "CLAUDE_TAP_TMUX_SESSION_NAME",
        "CLAUDE_TAP_TMUX_WINDOW_ID",
        "CLAUDE_TAP_TMUX_PANE_ID",
        "CLAUDE_TAP_SURFACE_ID",
        "CLAUDE_TAP_DECISION_TIMEOUT",
        "CLAUDE_TAP_ACTIVE",
        "CLAUDE_TAP_DISABLED",
    ]:
        monkeypatch.delenv(var, raising=False)
    return tmp_path
