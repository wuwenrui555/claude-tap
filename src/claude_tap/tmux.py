"""Read tmux context that the wrapper exported into env vars."""

import os

from .events import TmuxInfo


def read_tmux_info_from_env() -> TmuxInfo | None:
    """Build a TmuxInfo from CLAUDE_TAP_TMUX_* env vars.

    The wrapper is responsible for populating these vars. When all three
    are absent, the wrapper either was not invoked or did not detect
    tmux — return None so the event records `tmux: null`.
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
