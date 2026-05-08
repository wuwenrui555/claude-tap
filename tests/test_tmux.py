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
