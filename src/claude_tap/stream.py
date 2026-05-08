"""EventStream: async iterator over events.jsonl.

v0.1 uses short-interval file polling (default 100ms). The interface is
push-style from the consumer's perspective; `async for ev in EventStream()`
yields each new event as it lands. Latency is bounded by the poll
interval. v0.2 may switch to inotify-backed pushing for sub-millisecond
latency when there is a real requirement.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from .config import events_path

_DEFAULT_POLL_INTERVAL = 0.1  # 100 ms


class EventStream:
    """Async iterator over events.jsonl.

    Usage:
        async for event in EventStream():
            handle(event)
    """

    def __init__(
        self,
        path: Path | None = None,
        from_start: bool = False,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
    ):
        self._path = path or events_path()
        self._from_start = from_start
        self._poll_interval = poll_interval
        self._closed = False

    def close(self) -> None:
        self._closed = True

    async def __aiter__(self) -> AsyncIterator[dict]:
        # Whether the file already existed at subscribe time. If not,
        # any lines that appear belong to the consumer's window
        # (regardless of from_start).
        file_existed_at_subscribe = self._path.exists()

        while not self._path.exists() and not self._closed:
            await asyncio.sleep(self._poll_interval)
        if self._closed:
            return

        with open(self._path, encoding="utf-8") as f:
            if not self._from_start and file_existed_at_subscribe:
                f.seek(0, 2)  # end of file — skip pre-existing history

            buf = ""
            while not self._closed:
                line = f.readline()
                if not line:
                    await asyncio.sleep(self._poll_interval)
                    continue
                buf += line
                if buf.endswith("\n"):
                    try:
                        yield json.loads(buf.rstrip("\n"))
                    except json.JSONDecodeError:
                        pass  # skip malformed
                    buf = ""
