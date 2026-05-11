"""claude-tap: Claude Code → structured events + derived message stream."""

from . import drift
from ._version import __version__
from .events import SCHEMA_VERSION, ClaudeInfo, Event, TmuxInfo
from .listener import DecisionListener, DecisionRequest
from .messages import MessageStream
from .models import ClaudeMessage
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
    "ClaudeMessage",
    "MessageStream",
    "drift",
]
