"""MessageStream: derived per-message view of a Claude Code session.

Subscribes to ``events.jsonl`` (via :class:`EventStream`) as the
canonical trigger source and reads each session's transcript JSONL
incrementally to assemble the actual :class:`ClaudeMessage` stream.

Hook payload is the source of truth for everything it can produce
directly (user prompts, ``tool_use`` entries, ``ExitPlanMode`` plan,
``stop.last_assistant_message`` final reply). Transcript reads happen
in *narrow* mode and supply only what the hook stream cannot:
mid-turn pure-text assistant messages, ``tool_result`` contents
(including image bytes), and slash-command output.

Mid-turn assistant text has no dedicated hook event, and the OS
flush of those transcript lines often lags the hook fires that
bracket them — observed in the wild: an assistant text written
shortly before a ``pre_tool_use`` only became visible at the
matching ``post_tool_use`` minutes later when the tool was an
``AskUserQuestion`` that waited on the user. To keep that latency
low, after every event whose successor may be a fresh transcript
write — ``user_prompt_submit``, ``pre_tool_use``, ``post_tool_use``
— we spawn a short-lived per-session polling task that re-reads the
transcript at ``poll_interval`` until either the next event for
that session arrives (cancels it) or a 30 s safety deadline elapses
(self-stop). ``stop`` does not trigger polling because the turn has
ended; ``session_*`` / ``notification`` / ``permission_request``
are not message-progressing events. This is hook-anchored polling:
both the time window and the per-event scope are bounded.

Race characterization (2026-05-10): the transcript line for a
``pre_tool_use`` event is timestamped 60–90 ms before the hook
fires; reading at hook fire is reliable without retries. See
``docs/superpowers/specs/2026-05-10-claude-tap-v0.2-message-stream.md``.

State is in-memory only (per spec non-goal: no offset persistence).
The ``from_start=False`` default reads each session's transcript
from byte 0 on first observation but filters yielded messages by
timestamp — anything stamped before subscribe time is dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

from . import config
from .config import events_path
from .models import ClaudeMessage
from .stream import EventStream
from .tool_summary import format_tool_use_summary
from .transcript import (
    PROMPT_TOOL_INPUT_PASSTHROUGH,
    PendingTool,
    read_incremental,
)

logger = logging.getLogger(__name__)

# Per-session ring buffer size for dedup of hook-emitted text vs.
# transcript text emissions. Eight is far larger than the
# transcript-vs-hook lag we have ever observed; keeps memory bounded.
_HOOK_PROMPT_DEDUP_SIZE = 8

# Events that trigger a per-session scoped polling task. Polling
# catches transcript writes whose fs flush lagged the bracketing
# hook fire (e.g. mid-turn assistant text written before a
# pre_tool_use but flushed after it). ``stop`` is omitted because
# the turn has ended; ``session_*`` / ``notification`` /
# ``permission_request`` are not message-progressing.
_POLL_AFTER_EVENT_TYPES = frozenset(
    {"user_prompt_submit", "pre_tool_use", "post_tool_use"}
)


def _iso_to_unix(ts: str | None) -> float | None:
    """Parse an ISO-8601 timestamp into Unix epoch seconds, or None."""
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return None


_END_SENTINEL = object()


class MessageStream:
    """Async iterator over reconstructed ClaudeMessages.

    Usage::

        async for msg in MessageStream():
            handle(msg)

    Parameters
    ----------
    events_path_:
        Defaults to :func:`claude_tap.config.events_path`.
    from_start:
        If True, replay from the start of ``events.jsonl`` and read
        each session's transcript from offset 0 (no timestamp
        filtering). If False (default), the consumer's window starts
        at the moment of subscribe; per-session offsets initialize
        to 0 and the timestamp filter drops anything older than
        subscribe.
    poll_interval:
        Polling cadence (s) for both the underlying EventStream tail
        and the post-tool-use scoped polling task. Falls back to
        ``CLAUDE_TAP_POLL_INTERVAL`` (default 0.1) when ``None``.
    poll_max_duration:
        Safety bound on the per-session post-tool-use polling task;
        polling self-stops after this many seconds even if no next
        hook arrives. Falls back to ``CLAUDE_TAP_POLL_MAX_DURATION``
        (default 30) when ``None``.
    """

    def __init__(
        self,
        events_path_: Path | None = None,
        from_start: bool = False,
        poll_interval: float | None = None,
        poll_max_duration: float | None = None,
    ) -> None:
        self._events_path = events_path_ or events_path()
        self._from_start = from_start
        self._poll_interval = (
            poll_interval if poll_interval is not None else config.poll_interval()
        )
        self._poll_max_duration = (
            poll_max_duration
            if poll_max_duration is not None
            else config.poll_max_duration()
        )
        self._closed = False

        # Per-session state. Reset at the start of each __aiter__ run.
        self._offsets: dict[str, int] = {}
        self._pending: dict[str, dict[str, PendingTool]] = {}
        self._last_cmd: dict[str, str | None] = {}
        self._hook_prompts: dict[str, deque[str]] = {}
        self._poll_tasks: dict[str, asyncio.Task] = {}
        # Latest (session_name, window_id) observed for each session,
        # copied off the event envelope. Stamped on every emitted
        # ClaudeMessage so downstream renderers can show it.
        self._tmux_info: dict[str, tuple[str | None, str | None]] = {}
        self._subscribe_unix = 0.0
        self._lock: asyncio.Lock | None = None
        self._out_q: asyncio.Queue | None = None
        self._stream: EventStream | None = None
        self._consumer_task: asyncio.Task | None = None

    def close(self) -> None:
        self._closed = True
        if self._stream is not None:
            self._stream.close()

    async def __aiter__(self) -> AsyncIterator[ClaudeMessage]:
        # Reset per-iteration state.
        self._offsets = {}
        self._pending = {}
        self._last_cmd = {}
        self._hook_prompts = {}
        self._poll_tasks = {}
        self._tmux_info = {}
        self._subscribe_unix = time.time()
        self._lock = asyncio.Lock()
        self._out_q = asyncio.Queue()

        self._consumer_task = asyncio.create_task(self._event_consumer_loop())
        try:
            while True:
                item = await self._out_q.get()
                if item is _END_SENTINEL:
                    break
                yield item
        finally:
            await self._cleanup()

    async def _cleanup(self) -> None:
        """Cancel background tasks. Idempotent."""
        for task in list(self._poll_tasks.values()):
            if not task.done():
                task.cancel()
        for task in list(self._poll_tasks.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._poll_tasks.clear()

        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._consumer_task
        self._consumer_task = None

        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.close()
            self._stream = None

    async def _event_consumer_loop(self) -> None:
        """Pump events from EventStream into _process_event."""
        try:
            self._stream = EventStream(
                path=self._events_path,
                from_start=self._from_start,
                poll_interval=self._poll_interval,
            )
            async for event in self._stream:
                if self._closed:
                    break
                await self._process_event(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("MessageStream consumer loop crashed")
        finally:
            assert self._out_q is not None
            await self._out_q.put(_END_SENTINEL)

    async def _process_event(self, event: dict) -> None:
        """Handle one event end-to-end: cancel poll, transcript read, hook emit, maybe spawn poll."""
        claude = event.get("claude") or {}
        sid = claude.get("session_id") or ""
        tpath = claude.get("transcript_path") or ""
        if not sid or not tpath:
            return
        transcript_path = Path(tpath)
        et = event.get("event_type", "")
        payload = event.get("payload") or {}
        event_ts = event.get("timestamp")

        # Snapshot the tmux pane this session is currently on. Latest
        # observation wins — sessions can move panes between events.
        tmux = event.get("tmux") or {}
        sn = tmux.get("session_name") or None
        wid = tmux.get("window_id") or None
        if sn or wid:
            self._tmux_info[sid] = (sn, wid)
        event_passes_ts_filter = (
            self._from_start
            or _iso_to_unix(event_ts) is None
            or _iso_to_unix(event_ts) >= self._subscribe_unix
        )

        # An event arrived for this session — cancel any in-flight
        # post-tool-use polling task (its job is done; this event's
        # transcript read covers everything the poll would have
        # caught).
        old_task = self._poll_tasks.pop(sid, None)
        if old_task is not None and not old_task.done():
            old_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await old_task

        assert self._lock is not None
        assert self._out_q is not None
        async with self._lock:
            if sid not in self._offsets:
                self._offsets[sid] = 0
                self._pending[sid] = {}
                self._last_cmd[sid] = None

            # Pre-mark hook-emitted texts for dedup of redundant
            # transcript emissions of the same content.
            if et == "user_prompt_submit":
                prompt = payload.get("prompt", "")
                prompt = prompt.strip() if isinstance(prompt, str) else ""
                if prompt:
                    self._hook_prompts.setdefault(
                        sid, deque(maxlen=_HOOK_PROMPT_DEDUP_SIZE)
                    ).append(prompt)
            elif et == "stop":
                last_text = payload.get("last_assistant_message", "")
                last_text = last_text.strip() if isinstance(last_text, str) else ""
                if last_text:
                    self._hook_prompts.setdefault(
                        sid, deque(maxlen=_HOOK_PROMPT_DEDUP_SIZE)
                    ).append(last_text)

            await self._read_and_queue_locked(sid, transcript_path)

            # Hook-derived emissions, in chronological order after
            # any transcript content already queued above.
            if et == "user_prompt_submit" and event_passes_ts_filter:
                prompt = payload.get("prompt", "")
                prompt = prompt.strip() if isinstance(prompt, str) else ""
                if prompt:
                    await self._emit_locked(
                        sid,
                        ClaudeMessage(
                            session_id=sid,
                            role="user",
                            content_type="text",
                            text=prompt,
                            timestamp=event_ts,
                            source="hook",
                        ),
                    )
            elif et == "pre_tool_use" and event_passes_ts_filter:
                await self._emit_pre_tool_use_locked(
                    sid=sid, payload=payload, event_ts=event_ts
                )
            elif et == "stop" and event_passes_ts_filter:
                last_text = payload.get("last_assistant_message", "")
                last_text = last_text.strip() if isinstance(last_text, str) else ""
                if last_text:
                    await self._emit_locked(
                        sid,
                        ClaudeMessage(
                            session_id=sid,
                            role="assistant",
                            content_type="text",
                            text=last_text,
                            timestamp=event_ts,
                            source="hook",
                        ),
                    )

        # Spawn a scoped polling task after every message-progressing
        # hook so transcript writes whose fs flush lagged the hook
        # fire become visible within `poll_interval` rather than
        # piggybacking on the next hook (potentially many seconds
        # later, e.g. when a slow ``AskUserQuestion`` is in flight).
        # See ``_POLL_AFTER_EVENT_TYPES`` for the set.
        if et in _POLL_AFTER_EVENT_TYPES:
            self._poll_tasks[sid] = asyncio.create_task(
                self._poll_session(sid, transcript_path)
            )

    async def _emit_locked(self, sid: str, msg: ClaudeMessage) -> None:
        """Stamp cached tmux info onto ``msg`` and queue it. Caller holds the lock."""
        info = self._tmux_info.get(sid)
        if info:
            msg.tmux_session_name = info[0]
            msg.tmux_window_id = info[1]
        assert self._out_q is not None
        await self._out_q.put(msg)

    async def _emit_pre_tool_use_locked(
        self, *, sid: str, payload: dict, event_ts: str | None
    ) -> None:
        """Caller holds self._lock."""
        tool_use_id = payload.get("tool_use_id", "") or None
        tool_name = payload.get("tool_name", "") or ""
        tool_input = payload.get("tool_input", {})
        if not tool_name:
            return
        if tool_name == "ExitPlanMode" and isinstance(tool_input, dict):
            plan = tool_input.get("plan", "")
            if isinstance(plan, str) and plan.strip():
                await self._emit_locked(
                    sid,
                    ClaudeMessage(
                        session_id=sid,
                        role="assistant",
                        content_type="text",
                        text=plan.strip(),
                        timestamp=event_ts,
                        source="hook",
                    ),
                )
        summary = format_tool_use_summary(tool_name, tool_input)
        input_passthrough = (
            tool_input
            if isinstance(tool_input, dict)
            and tool_name in PROMPT_TOOL_INPUT_PASSTHROUGH
            else None
        )
        await self._emit_locked(
            sid,
            ClaudeMessage(
                session_id=sid,
                role="assistant",
                content_type="tool_use",
                text=summary,
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                input=input_passthrough,
                timestamp=event_ts,
                source="hook",
            ),
        )

    async def _read_and_queue_locked(self, sid: str, transcript_path: Path) -> None:
        """Read transcript incrementally and queue qualifying messages.

        Caller must hold ``self._lock``.
        """
        messages, new_offset, new_pending, new_last_cmd = read_incremental(
            transcript_path,
            session_id=sid,
            last_offset=self._offsets[sid],
            pending_tools=self._pending[sid],
            last_cmd_name=self._last_cmd[sid],
            narrow=True,
        )
        self._offsets[sid] = new_offset
        self._pending[sid] = new_pending
        self._last_cmd[sid] = new_last_cmd

        for msg in messages:
            if msg.content_type == "thinking":
                continue
            if not self._from_start:
                msg_unix = _iso_to_unix(msg.timestamp)
                if msg_unix is not None and msg_unix < self._subscribe_unix:
                    continue
            if msg.content_type == "text":
                dq = self._hook_prompts.get(sid)
                if dq and msg.text in dq:
                    continue
            await self._emit_locked(sid, msg)

    async def _poll_session(self, sid: str, transcript_path: Path) -> None:
        """Periodically read transcript until cancelled or deadline.

        Spawned after any message-progressing event (see
        ``_POLL_AFTER_EVENT_TYPES``) for one session. Cancelled when
        the next event for that session arrives. Self-stops at
        ``poll_max_duration`` to bound runaway polling.
        """
        deadline = time.time() + self._poll_max_duration
        try:
            while time.time() < deadline:
                await asyncio.sleep(self._poll_interval)
                assert self._lock is not None
                async with self._lock:
                    await self._read_and_queue_locked(sid, transcript_path)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("MessageStream poll loop crashed for %s", sid)
