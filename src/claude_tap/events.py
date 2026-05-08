"""Event schema and atomic JSONL append."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ClaudeInfo:
    session_id: str
    transcript_path: str
    cwd: str
    permission_mode: str


@dataclass(frozen=True)
class TmuxInfo:
    session_name: str
    window_id: str
    pane_id: str


@dataclass(frozen=True)
class Event:
    event_type: str
    timestamp: str
    claude: ClaudeInfo
    tmux: TmuxInfo | None
    surface_id: str
    payload: dict[str, Any]
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "claude": asdict(self.claude),
            "tmux": asdict(self.tmux) if self.tmux is not None else None,
            "surface_id": self.surface_id,
            "payload": self.payload,
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False) + "\n"


def now_isoformat() -> str:
    """Current time in ISO 8601 with UTC offset."""
    return datetime.now(UTC).isoformat()


def append_jsonl(path: Path, event: Event) -> None:
    """Append one event line atomically.

    O_APPEND on POSIX guarantees single-write atomicity for writes ≤
    PIPE_BUF (≥ 4 KB on Linux). Lines are kept short by the schema;
    payload extraction never embeds full transcript content.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = event.to_jsonl().encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
