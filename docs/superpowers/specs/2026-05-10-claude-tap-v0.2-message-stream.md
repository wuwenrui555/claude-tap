<!-- markdownlint-disable MD024 -->

# claude-tap v0.2: derived ClaudeMessage stream

- **Date**: 2026-05-10
- **Repo**: `claude-tap`
- **Status**: design draft, awaiting user review
- **Targets**: claude-tap v0.2.0

## Revision history

- **2026-05-10 (initial)** — initial design after empirical race
  measurement and pivot from a separate `ccmux-backend-2` package.

## Context

Downstream consumers that want a per-message view of a Claude Code
session — Telegram relays, IDE chat panels, observability dashboards
— have so far had two options:

1. **Read `~/.claude/projects/*.jsonl` transcripts directly**, with all
   the byte-offset bookkeeping, mtime cache invalidation, file
   rotation, and JSONL parsing that entails. The original
   `ccmux-backend` codebase took this route and grew significant
   complexity to make it reliable
   (`ccmux-backend/src/ccmux/message_monitor.py` and
   `claude_transcript_parser.py`, ~1300 lines combined).
2. **Reconstruct messages from claude-tap hook events alone**, which
   v0.1.3 made nearly possible by surfacing `last_assistant_message`,
   `tool_use_id`, and `duration_ms`. But it is **not** sufficient: a
   Claude turn that ends on a tool call never fires `Stop`, so any
   pure-text assistant message that appears mid-turn is invisible to
   the hook stream. Section [Empirical
   findings](#empirical-findings-2026-05-10) shows mid-turn
   pure-text messages are 63% of all pure-text assistant messages —
   not an edge case.

This spec adds a third, better option: **claude-tap itself produces
the per-message stream**, hiding the transcript machinery from
consumers. A consumer that wants events keeps using
`claude_tap.EventStream`. A consumer that wants messages uses the new
`claude_tap.MessageStream`. Both subscribe to the same underlying
state and can run in the same process.

## Charter shift from v0.1.3

The v0.1.3 spec
([`2026-05-09-tap-v0.1.3-message-fields.md`](2026-05-09-tap-v0.1.3-message-fields.md))
explicitly carved out:

> **No streaming / partial assistant text.** Hooks fire at turn
> boundaries; mid-stream text never reaches a hook. If a consumer
> wants typewriter-style display, it still needs to tail
> `transcript_path`. claude-tap is not the right layer for that.

That non-goal still applies to **character-level streaming**. v0.2
does not change it: claude-tap is not becoming a typewriter renderer.
What v0.2 does change is the second sentence — claude-tap is now
**also** the right layer for assembling per-message views, because:

1. **The transcript-reading code is identical for every consumer.**
   Pushing it into claude-tap removes a duplicated subsystem from each
   downstream package.
2. **claude-tap already knows the hook timing.** The race window
   between `pre_tool_use` firing and the corresponding assistant
   message being flushed to the transcript is a property of Claude
   Code's internal sequencing, not of any consumer. Encoding the
   "wait until transcript is written" knowledge once, in claude-tap,
   is more robust than asking each consumer to rediscover it.
3. **No consumer of v0.1.x events is broken.** v0.2 is purely
   additive: the existing `events.jsonl` writer and `EventStream`
   reader keep their v0.1 contract verbatim. v0.2 adds a new module
   alongside.

The original "faithful hook-stream forwarder" identity is preserved
for `EventStream`. The new identity, "Claude Code session observer
with derived message stream," extends that without contradicting it.

## Empirical findings (2026-05-10)

The pivot to v0.2 was motivated by a post-hoc analysis of the user's
own `~/.claude-tap/events.jsonl` (1619 events) and the corresponding
session transcripts.

### Race window: `pre_tool_use` vs. transcript flush

For each `pre_tool_use` event carrying `payload.tool_use_id` (n=89),
we located the assistant-message JSONL line with that id and computed
`fire_ts - msg_ts`. Positive deltas mean the transcript was written
**before** the hook fired.

| Statistic | Value |
|---|---|
| n | 89 |
| min | +0.061 s |
| p05 | +0.062 s |
| p50 | +0.064 s |
| p95 | +0.076 s |
| max | +0.092 s |
| negative (race) cases | 0 / 89 (0.0%) |

The hook always fires 60–90 ms after the transcript is written. A
narrow read at hook-fire time is safe with no retries.

### Assistant-message shapes

Across 1853 assistant messages from 4 recent transcripts, the only
shapes observed were:

| Shape | Count |
|---|---|
| pure `tool_use` | 994 |
| pure `thinking` | 460 |
| pure `text` | 399 |

**Mixed `text` + `tool_use` in the same JSONL line: 0.** Claude Code
2.1.x does not interleave text and tool_use blocks within one
assistant message.

### Mid-turn narration is real and frequent

For each pure-text assistant message, we classified it by what comes
after it in the transcript:

| Classification | Count |
|---|---|
| followed by another assistant message containing `tool_use` | 252 |
| followed by a user prompt (i.e., this was the final reply) | 145 |
| EOF (session in progress) | 3 |

**63% of pure-text assistant messages are mid-turn narration**, not
final replies. These are messages like "我先探一下现有代码" or "好问题，
这个区分很关键" — substantively informative. Hook events alone do
not surface them; only the transcript does.

### Implications

- Reading the transcript at hook-fire time is reliable (race-free in
  this dataset).
- The information that the hook stream cannot deliver is real and
  high-frequency.
- The right place to bridge those two facts is claude-tap itself.

## Goals (v0.2)

1. Add a new `claude_tap.MessageStream` async iterator that yields
   `ClaudeMessage` records reconstructed from the same session's
   `events.jsonl` (as triggers) and Claude Code transcript JSONL (as
   source of truth).
2. Add a `claude-tap watch-messages` CLI subcommand that runs
   `MessageStream` and dumps each message as JSON Lines to stdout.
   Convenient for debugging, forensic capture, and quick consumers
   that prefer pipes to imports.
3. Preserve `EventStream` and `events.jsonl` exactly as v0.1.x. No
   change to existing consumers (`ccmux-state` etc.) is required.
4. Reuse the JSONL parsing primitives that `ccmux-backend` already
   has — port the pure parts (`parse_entries`, tool-summary
   formatters, image extraction) instead of rewriting.
5. Make `MessageStream` correct under multi-session conditions
   (subagents, multiple parallel `claude` instances) by routing on
   `claude.session_id`.

## Non-goals (v0.2)

- **No streaming / partial assistant text** (carried over from v0.1.3
  non-goal #1; this is character-level streaming, still out of
  scope).
- **No `thinking` blocks in the message stream.** Carried over from
  v0.1.3 non-goal #2. `MessageStream` skips `thinking` content per
  the existing project policy.
- **No persisted offset state on disk.** `MessageStream` keeps
  per-session byte offsets in process memory. Restart resets all
  offsets to current file size; no replay of older messages. A
  durable `messages.jsonl` is explicitly **not** written by default.
- **No `messages.jsonl` background daemon.** v0.2 does not introduce
  a new long-running process. The CLI dump (`watch-messages`) is the
  one supported way to materialize messages to disk, and it is
  user-launched.
- **No history / `/list` API.** Listing past sessions and replaying
  archived messages remains out of scope; `MessageStream` only emits
  messages produced after the consumer subscribes.
- **No image-format conversion.** `tool_result` images are surfaced
  as `(media_type, raw_bytes)` tuples, exactly as the existing
  ccmux-backend parser does. Consumers handle their own encoding.
- **No automatic subagent topic routing.** Subagent sessions are
  emitted with their own `session_id`; consumers decide whether and
  how to display them. (See [Multi-session](#multi-session-and-subagents).)

## Architecture

```
                          ┌─────────────────────────┐
                          │   claude (wrapped)       │
                          └────────────┬─────────────┘
                                       │ hook fires + writes transcript
                                       ▼
              ┌───────────────────────────────────────────────┐
              │   ~/.claude-tap/events.jsonl  +                │
              │   ~/.claude/projects/.../<session>.jsonl       │
              └────────────┬───────────────────┬──────────────┘
                           │                   │
                  reads via│             reads │incrementally
                           ▼                   ▼
                ┌──────────────────┐  ┌────────────────────┐
                │  EventStream     │  │  MessageStream     │
                │  (v0.1.x)        │  │  (v0.2 NEW)        │
                └────────┬─────────┘  └─────────┬──────────┘
                         │                      │
                  yield  │                yield │
                         ▼                      ▼
                  raw Event dict          ClaudeMessage
                  (consumers: ccmux-      (consumers: telegram
                   state, ccmux-monitor)   relay, future GUIs)
```

`MessageStream` internally subscribes to `EventStream` (or directly
tails `events.jsonl` — implementation detail), and per session keeps
a `transcript_path → byte_offset` map in memory. On every event with
a `claude.transcript_path`, it does an incremental read of the
transcript from the saved offset to current EOF, parses any new
JSONL lines into `ClaudeMessage`s using the ported logic, advances
the offset, and yields each new message in transcript order.

### Hook-anchored scoped polling

Mid-turn pure-text assistant messages have no dedicated hook event,
and even content that *does* trigger a hook can have its
transcript fs-flush lag the hook fire by an unpredictable amount.
Empirical example: an assistant text written shortly before a
`pre_tool_use` was not yet flushed at hook fire and only became
visible at the matching `post_tool_use` minutes later, when the
tool was an `AskUserQuestion` waiting on the user.

To bound that latency without reintroducing global polling,
`MessageStream` spawns a **per-session scoped polling task** after
every message-progressing event — `user_prompt_submit`,
`pre_tool_use`, and `post_tool_use`. The task re-reads the
transcript at `poll_interval` and emits any new content. It is
cancelled the moment the next event for that session arrives (its
job is then covered by that event's transcript read), and
self-stops at `poll_max_duration` (default 30 s) so a stuck or
idle session does not leak a polling task.

`stop` does not trigger polling because the turn has ended;
`session_*` / `notification` / `permission_request` are not
message-progressing events.

The result: any transcript-only content (mid-turn text, late-flush
final replies, etc.) becomes visible within `poll_interval`
(~100 ms) of being written, while polling activity is bounded in
time (only between message-progressing events) and in scope (per
session, cancellable). Sessions in an idle or end-of-turn state do
no polling at all.

### New / changed files

```
src/claude_tap/
  __init__.py        — re-export ClaudeMessage, MessageStream (CHANGED)
  events.py          — unchanged
  hook.py            — unchanged
  stream.py          — unchanged (EventStream)
  cli.py             — add watch-messages subcommand (CHANGED)
  config.py          — unchanged
  drift.py           — unchanged
  listener.py        — unchanged
  socket_proto.py    — unchanged
  tmux.py            — unchanged
  wrapper.py         — unchanged

  models.py          — NEW. ClaudeMessage dataclass.
  transcript.py      — NEW. Incremental JSONL read + parse_entries port.
  tool_summary.py    — NEW. Tool-name → summary string formatter.
  messages.py        — NEW. MessageStream class.
```

Estimated new code: ~700–900 lines total, after porting and
simplifying. The existing 1197 lines stay essentially unchanged.

## Data model: `ClaudeMessage`

Verbatim port from
`ccmux-backend/src/ccmux/claude_transcript_parser.py:38-65` so that
existing downstream code (e.g. `ccmux-telegram` which already uses
this shape) ports with one import line change.

```python
from dataclasses import dataclass
from typing import Literal


@dataclass
class ClaudeMessage:
    """A single message reconstructed from a Claude Code session.

    Fields are display-oriented; `text` is already formatted for direct
    rendering by a markdown-aware consumer. Tool pairing is exposed via
    `tool_use_id` so downstream consumers can correlate a tool_use entry
    with its tool_result entry without re-parsing the transcript.
    """

    session_id: str
    role: Literal["user", "assistant"]
    content_type: Literal[
        "text", "thinking", "tool_use", "tool_result", "local_command"
    ]
    text: str
    tool_use_id: str | None = None
    tool_name: str | None = None
    input: dict | None = None              # populated only for prompt tools
    image_data: list[tuple[str, bytes]] | None = None
    timestamp: str | None = None           # ISO from transcript JSONL
    is_complete: bool = True               # always True in v0.2 (no streaming)
```

### v0.2-specific simplifications

- `content_type == "thinking"` is **never emitted** by MessageStream
  (per non-goal). The literal type still includes it so consumers
  written against the broader shape do not need to narrow.
- `is_complete` is **always `True`**. Kept for source-compatibility
  with consumers that already check it; can be removed in a future
  major version.
- `timestamp` is the ISO timestamp from the transcript JSONL line,
  carried through unchanged.

## `MessageStream` API

```python
class MessageStream:
    """Async iterator over reconstructed ClaudeMessages.

    Subscribes to events.jsonl as triggers and reads transcript files
    incrementally as the source of truth.

    Usage:
        async for msg in MessageStream():
            handle(msg)

    Args:
        events_path: defaults to claude_tap.config.events_path()
        from_start: if True, replay all events in events.jsonl from
            the beginning. Default False (current EOF). Ignored if
            events.jsonl does not yet exist; then the consumer's
            window starts at the first event written after subscribe.
        poll_interval: events.jsonl polling cadence (s); default 0.1.
    """

    def __init__(
        self,
        events_path: Path | None = None,
        from_start: bool = False,
        poll_interval: float = 0.1,
    ) -> None: ...

    def close(self) -> None: ...

    def __aiter__(self) -> AsyncIterator[ClaudeMessage]: ...
```

### `from_start` semantics

`from_start=False` (default): `MessageStream` records the wall-clock
subscribe time at the start of iteration. Every session's offset is
initialized to **byte 0** on first observation; messages whose
transcript timestamp is before subscribe time are filtered out at
yield. This handles two scenarios correctly:

* **Fresh subscriber, fresh sessions.** Transcript files are empty
  or didn't exist at subscribe; everything written afterwards has a
  post-subscribe timestamp and passes the filter.
* **Reconnect mid-session.** Transcript already has history at
  subscribe; old lines fail the timestamp filter and are dropped,
  while any line written after subscribe (including content written
  in the brief window between transcript flush and hook fire)
  passes.

The cost is a single full-transcript read on the first event for
each session. Acceptable for v0.2; an offset-snapshot optimisation
can land in v0.2.x if profiles show it matters.

`from_start=True`: replays from the beginning of `events.jsonl`,
disables the timestamp filter, and the full transcript of every
session gets parsed and yielded. Useful for forensic reconstruction.

### Yielding order

Within a single session, messages are yielded in transcript JSONL
order (which matches Claude's conversational order). Across
sessions, ordering is interleaved by the order of triggering events
in `events.jsonl` — which is itself the wall-clock order of hook
fires. No total order guarantee across sessions beyond that.

## CLI: `claude-tap watch-messages`

```
claude-tap watch-messages [--from-start] [--events PATH]
```

Runs `MessageStream` and writes each `ClaudeMessage` to stdout as one
JSON line. Field names match the dataclass; `image_data` is rendered
as `[(media_type, base64_string), ...]` (base64 because raw bytes
are not JSON-serializable). One message per line. Mirrors the style
of any number of standard log-tailing commands; pipe-friendly.

Existing CLI subcommands (the ones currently in `cli.py`) are
unchanged.

## Internal: `transcript.py`

Incremental read + parse for one session's transcript JSONL.

### Responsibilities

1. Open the transcript at `transcript_path`.
2. Seek to `last_offset`.
3. Read lines up to current EOF.
4. Parse each new line through the ported `parse_entries` logic into
   `ClaudeMessage` objects.
5. Update `last_offset` to the post-read position.
6. Return the list of new `ClaudeMessage`s.

### Port plan (concrete)

From `ccmux-backend/src/ccmux/claude_transcript_parser.py`:

| Old code | New location | Notes |
|---|---|---|
| `parse_entries` (lines 446–865) | `transcript.py` | core JSONL → ClaudeMessage logic; keeps the `pending_tools` carry-over so tool_use → tool_result pairing works across reads |
| `parse_message` (lines 307–362) | `transcript.py` | helper; needed by `parse_entries` |
| `extract_text_only` (lines 149–181) | `transcript.py` | text block extraction |
| `extract_tool_result_text` (lines 262–277) | `transcript.py` | tool_result text extraction |
| `extract_tool_result_images` (lines 279–305) | `transcript.py` | base64 → bytes |
| `_format_blockquote` (lines 369–379) | `transcript.py` | markdown formatting |
| `_format_edit_diff` (lines 193–206) | `transcript.py` | Edit-tool diff |
| `_NO_CONTENT_PLACEHOLDER`, `_INTERRUPTED_TEXT`, `_MAX_SUMMARY_LENGTH`, `_RE_*` | `transcript.py` | constants and regexes |
| `format_tool_use_summary` (lines 208–260) | `tool_summary.py` | tool-name → display string |
| `_format_tool_result_text` (lines 381–444) | `tool_summary.py` | per-tool stats line |

The port is a copy with no behavioral change. The dependency
`from claude_code_state import config as _pc` (used in
`format_tool_use_summary`) is satisfied by adding `claude-code-state`
to claude-tap's dependencies, or by inlining the small set of
constants it provides if claude-code-state would be too heavy a
dependency for a single config dict (decision deferred to
implementation).

### Offset state

In-memory only: `dict[session_id, int]`. On the first event observed
for a given `session_id`, the offset is initialized to the
transcript's current file size (per `from_start` semantics above).

### Failure modes

| Condition | Behavior |
|---|---|
| `transcript_path` does not exist | yield nothing, leave offset at 0; retry on next event |
| `transcript_path` exists but file shrank since last read (truncation / rotation) | reset offset to 0, log a warning to stderr, continue |
| line is not valid JSON | skip the line (existing parser already does this) |
| line is JSON but not a recognized message type | skip per existing parser |
| OS error reading file (permission, vanished mid-read) | yield nothing for this trigger, leave offset unchanged, retry on next event |

`MessageStream` does not raise on transcript-side problems. It
degrades to "fewer messages emitted" rather than terminating.

## Internal: `tool_summary.py`

Pure functions, no I/O, no state. Two entry points correspond to
`format_tool_use_summary` and `_format_tool_result_text` from the
old parser. Used by `transcript.py` during JSONL parsing.

## Internal: `messages.py`

`MessageStream` orchestrator. Pseudocode:

```python
class MessageStream:
    def __init__(self, ...): ...

    async def __aiter__(self):
        offsets: dict[str, int] = {}             # session_id -> offset
        pending: dict[str, dict[str, PendingToolInfo]] = {}  # session_id -> pending_tools
        last_cmd: dict[str, str | None] = {}     # session_id -> last_cmd_name

        async for event in EventStream(self._events_path,
                                       from_start=self._from_start,
                                       poll_interval=self._poll_interval):
            sid = event.get("claude", {}).get("session_id")
            tpath = event.get("claude", {}).get("transcript_path")
            if not sid or not tpath:
                continue

            if sid not in offsets:
                # First time we see this session: snap to current EOF
                # (or 0 if from_start).
                offsets[sid] = 0 if self._from_start else current_size(tpath)

            new_messages, offsets[sid], pending[sid], last_cmd[sid] = (
                read_incremental(
                    transcript_path=tpath,
                    session_id=sid,
                    last_offset=offsets[sid],
                    pending_tools=pending.get(sid, {}),
                    last_cmd_name=last_cmd.get(sid),
                )
            )
            for msg in new_messages:
                if msg.content_type == "thinking":
                    continue                      # non-goal
                yield msg

    def close(self): ...
```

`read_incremental` is the new public function in `transcript.py`. It
returns `(messages, new_offset, new_pending_tools, new_last_cmd_name)`.

`pending_tools` carry-over is the same mechanism the old parser uses
to bridge a `tool_use` whose `tool_result` arrives in a later read.
This keeps tool pairing correct across read boundaries.

## Multi-session and subagents

Routing key is `event.claude.session_id`. Top-level Claude sessions
and subagent sessions both carry their own session_id. v0.2 does not
distinguish them: each becomes a separate stream of `ClaudeMessage`s,
and the consumer routes by `msg.session_id`.

If a consumer (e.g., a Telegram relay) only binds a topic to the
parent session_id, it sees only the parent's messages — including a
`Task` `tool_use` and its `tool_result`, but not the subagent's
internal steps. To observe subagent internals, the consumer would
have to bind to the subagent's session_id; v0.2 does not provide
that binding mechanism (out of scope, see non-goals).

`agent_id` and `agent_type` (added to claude-tap's optional set in
v0.1.3 for drift purposes) are not surfaced in `ClaudeMessage` for
v0.2. Consumers that want to know "this session is a subagent of
session X" can read the `claude.session_id` -> parent relationship
from `events.jsonl` directly, but that is an EventStream concern,
not a MessageStream one.

## Backwards compatibility

| Surface | v0.1.x → v0.2 |
|---|---|
| `events.jsonl` schema | unchanged |
| `EventStream` API | unchanged |
| `claude_tap.Event`, `ClaudeInfo`, `TmuxInfo` | unchanged |
| `claude_tap.DecisionListener`, `DecisionRequest` | unchanged |
| `claude-tap-hook` CLI | unchanged |
| `claude-tap` CLI subcommands existing in v0.1 | unchanged |
| `__init__.py` re-exports | **adds** `ClaudeMessage`, `MessageStream`. Removes nothing. |

A v0.1.x consumer compiled against v0.1.3 keeps working on v0.2.0
unchanged.

## Disposition of `ccmux-backend-2`

The original plan was to build a new `ccmux-backend-2` package as
the message-reconstruction layer. With v0.2, that responsibility
moves into claude-tap and `ccmux-backend-2` is **not created**. The
existing `ccmux-backend` package continues to exist (it owns
state-log, event-log, tmux integration, and other non-message
concerns) until those concerns are themselves migrated; there is no
forced migration deadline in this spec.

Downstream packages currently importing
`from ccmux.api import ClaudeMessage` (notably `ccmux-telegram`)
will switch to `from claude_tap import ClaudeMessage`. The dataclass
shape is identical, so this is a one-line import change plus
dropping a dependency on the heavy `ccmux-backend` for consumers
that only needed `ClaudeMessage`. That migration is a separate piece
of work, not part of this spec.

## Open questions

1. **Tool input passthrough scope.** The old parser only passes
   `input` through for `AskUserQuestion` and `ExitPlanMode` (the
   "prompt tools"). v0.2 should keep that behavior identically. If a
   future consumer wants `Edit` input passthrough as well, that's a
   v0.2.x additive change.
2. **Race for `user_prompt_submit`.** ~~We only measured race for
   `pre_tool_use`...~~ Resolved during implementation: empirical
   testing showed the user-line transcript timestamp is written
   before hook fire (same direction as `pre_tool_use`), but the
   OS-level fs flush often lags by enough that an immediate read
   misses it; the line only becomes visible on the next hook fire
   (typically `stop`, after Claude has finished responding —
   producing user-visible delay of seconds). The same lag affects
   `tool_use` and final replies. Resolution: `MessageStream` runs
   transcript reads in a **narrow** mode that suppresses transcript
   emission of any content the hook payload can produce, and emits
   the content directly from the hook event:

   * `user_prompt_submit.prompt` → user text
   * `pre_tool_use.tool_input` → `tool_use` ClaudeMessage (and
     `ExitPlanMode` plan emission as a separate text)
   * `stop.last_assistant_message` → final reply text

   `tool_result` content (per-tool stats, image data,
   error/interrupted detection) still comes from transcript because
   the hook's `tool_response` is a per-tool structured dict, not the
   model-visible block-list shape. Mid-turn pure-text assistant
   messages and local-command output also stay transcript-only;
   nothing in the hook stream surfaces them.

   Dedup: a per-session ring buffer of recently hook-emitted texts
   suppresses any transcript emission that mirrors them (covers both
   user prompts and final replies, since both can show up in
   transcript after hook fire).
3. **`SessionEnd` semantics.** When does claude-tap stop tracking a
   session's offset state? Probably on `session_end` event. Spec
   defers this to implementation; the cost of leaking offsets in a
   long-running consumer is small.
4. **`claude-code-state` dependency.** `format_tool_use_summary`
   reads a config dict from `claude_code_state`. Importing that
   package into claude-tap pulls in tmux-related dependencies that
   are only loosely related to message reconstruction. Implementer
   should evaluate whether to inline the relevant constants or take
   the dependency.

## Testing

### Unit tests

1. **`transcript.py: read_incremental`** — table-driven cases:
   - Empty transcript at start, message appended → reads new
     message, updates offset.
   - tool_use line followed by tool_result line in same read →
     pairing works, both yielded with correct `tool_use_id`.
   - tool_use in read N, tool_result in read N+1 → `pending_tools`
     carry-over yields tool_result with the correct summary.
   - File shrank mid-stream → offset reset to 0, warning logged.
   - Non-JSON line → skipped, no crash.
2. **`tool_summary.py`** — golden-string outputs for `Read`, `Bash`,
   `Edit` (input only), `Edit` (with diff), `TodoWrite`,
   `AskUserQuestion`, `ExitPlanMode`, `Write`, `Glob`, `Grep`,
   `Task`, `WebFetch`, `WebSearch`.
3. **`MessageStream`** — feed a synthetic `events.jsonl` and
   matching synthetic transcript file via temp paths; assert the
   yielded `ClaudeMessage` sequence matches expectations.
4. **`thinking` block suppression** — transcript with thinking
   blocks; assert MessageStream emits zero `content_type=="thinking"`.

### Integration test

Replay the user's existing
`~/.claude-tap/events.jsonl` (or a captured copy) plus the
corresponding transcripts through `MessageStream` with
`from_start=True`. Assert:

- The number of yielded messages is consistent with the number of
  user-visible exchanges in the captured session.
- Every `tool_use` ClaudeMessage has a matching `tool_result` (or
  is the trailing entry of an in-progress turn).
- `last_assistant_message` from each `stop` event in events.jsonl
  is byte-identical to the `text` of the corresponding final reply
  ClaudeMessage.

### CLI smoke test

`claude-tap watch-messages` against a live wrapped session: trigger
one tool call, confirm the JSONL output contains a `tool_use`
message, a `tool_result` message, and a final `text` message at end
of turn.

### Drift / regression

Add the existing `events.jsonl` snapshot used for analysis
(89-event window with race + mid-turn statistics) to
`tests/fixtures/` or similar. A test asserts the analysis script's
findings (race=0/89, mixed text+tool_use=0, etc.) hold against this
fixture, so any future refactor that changes parse semantics gets
flagged.

## Release plan

Standard git-flow:

1. Branch `feat/v0.2-message-stream` off `dev`.
2. Implement in this order: `models.py` → `tool_summary.py` →
   `transcript.py` → `messages.py` → CLI subcommand → `__init__.py`
   re-exports.
3. Each module gets unit tests in `tests/` before the next module
   starts.
4. Open PR to `dev`; verify CI green (ruff / pyright / pytest).
5. Merge `dev` to `main` via release branch when ready: bump version
   to `0.2.0` in `pyproject.toml`, `_version.py`,
   `tests/test_skeleton.py`. Update `CHANGELOG.md` with the
   user-facing additions and the charter-shift note.
6. Tag `v0.2.0`. Push.
7. Pre-push verification matches v0.1.x conventions.

No special migration step is required for v0.1.x consumers.

## Future work (deferred, not in scope)

- **`messages.jsonl` durable stream** (the "B" delivery model from
  the brainstorm): write derived messages to a durable file so
  consumers can survive backend restarts without losing recent
  history. Wait for a real consumer use case.
- **inotify-backed events.jsonl polling**: existing v0.1 stream uses
  100 ms polling. If a consumer needs sub-millisecond message
  latency, switch to inotify or kqueue. Not motivated by current
  consumers.
- **Streaming partial assistant text**: the v0.1.3 carve-out remains.
  Adding this would require a transcript line-truncation watcher,
  not a hook listener.
- **Subagent ↔ parent linking surface**: provide a documented way to
  ask "which subagent sessions belong to parent session X?" without
  the consumer parsing `events.jsonl` directly.
- **History API (`/list`-style)**: enumerate prior sessions and
  replay their messages. Phase-2 concern.
