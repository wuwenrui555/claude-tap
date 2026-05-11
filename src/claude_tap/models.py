"""Per-message data model for the v0.2 derived stream.

`ClaudeMessage` is the unit yielded by :class:`claude_tap.MessageStream`.
The shape mirrors `ccmux.api.ClaudeMessage` from the legacy
ccmux-backend so that consumers already wired against that contract
(notably ccmux-telegram) port with a one-line import change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class ClaudeMessage:
    """A single message reconstructed from a Claude Code session.

    Fields are display-oriented; `text` is already formatted for direct
    rendering by a markdown-aware consumer. Tool pairing is exposed via
    `tool_use_id` so downstream consumers can correlate a tool_use entry
    with its tool_result entry without re-parsing the transcript.

    `is_complete` is retained for source-compatibility with consumers
    that already check it; in v0.2 it is always True (the message
    stream emits no streaming partials).
    """

    session_id: str
    role: Literal["user", "assistant"]
    content_type: Literal[
        "text", "thinking", "tool_use", "tool_result", "local_command"
    ]
    text: str
    tool_use_id: str | None = None
    tool_name: str | None = None
    input: dict | None = None
    image_data: list[tuple[str, bytes]] | None = None
    timestamp: str | None = None
    is_complete: bool = True
    # Latest tmux pane the session was observed on, copied from the
    # event envelope at emit time. None when the session is not in
    # tmux or no event has been observed yet.
    tmux_session_name: str | None = None
    tmux_window_id: str | None = None
