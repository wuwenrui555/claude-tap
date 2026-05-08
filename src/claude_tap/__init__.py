"""claude-tap: Claude Code → structured events + decision bridge."""

from . import drift
from ._version import __version__
from .events import SCHEMA_VERSION, ClaudeInfo, Event, TmuxInfo
from .listener import DecisionListener, DecisionRequest
from .stream import EventStream

__all__ = [
    "__version__",
    "SCHEMA_VERSION",
    "Event",
    "ClaudeInfo",
    "TmuxInfo",
    "EventStream",
    "DecisionListener",
    "DecisionRequest",
    "drift",
]
