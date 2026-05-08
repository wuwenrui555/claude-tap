"""DecisionListener: bind decision.sock and answer PermissionRequests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import decision_sock_path


@dataclass(frozen=True)
class DecisionRequest:
    request_id: str
    session_id: str
    tool_name: str
    tool_input: dict[str, Any]
    permission_suggestions: list = field(default_factory=list)


class DecisionListener:
    """Async server bound to decision.sock.

    Usage:
        async with DecisionListener() as listener:
            async for req in listener:
                decision = await your_logic(req)
                await listener.respond(req.request_id, decision)
    """

    def __init__(self, path: Path | None = None):
        self._path = path or decision_sock_path()
        self._server: asyncio.Server | None = None
        self._queue: asyncio.Queue[DecisionRequest] | None = None
        self._writers: dict[str, asyncio.StreamWriter] = {}

    @property
    def path(self) -> Path:
        return self._path

    async def __aenter__(self) -> DecisionListener:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            self._path.unlink()
        self._queue = asyncio.Queue()
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self._path),
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        for w in list(self._writers.values()):
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass
        self._writers.clear()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            line = await reader.readline()
            if not line:
                writer.close()
                await writer.wait_closed()
                return
            payload = json.loads(line.decode("utf-8"))
            req = DecisionRequest(
                request_id=payload.get("request_id", ""),
                session_id=payload.get("session_id", ""),
                tool_name=payload.get("tool_name", ""),
                tool_input=payload.get("tool_input", {}),
                permission_suggestions=payload.get("permission_suggestions", []),
            )
            self._writers[req.request_id] = writer
            assert self._queue is not None
            await self._queue.put(req)
            # Keep writer open until respond() closes it.
        except (json.JSONDecodeError, ConnectionError):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def __aiter__(self) -> AsyncIterator[DecisionRequest]:
        assert self._queue is not None
        while True:
            req = await self._queue.get()
            yield req

    async def respond(self, request_id: str, decision: dict[str, Any]) -> None:
        """Send a decision response for a pending request."""
        writer = self._writers.pop(request_id, None)
        if writer is None:
            return
        try:
            response = {"request_id": request_id, "decision": decision}
            writer.write(
                (json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8")
            )
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
