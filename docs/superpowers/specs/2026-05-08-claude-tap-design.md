<!-- markdownlint-disable MD024 -->

# claude-tap design

- **Date**: 2026-05-08
- **Repo**: `claude-tap` (new, standalone)
- **Status**: design draft, awaiting user review

## Revision history

- **2026-05-08 (initial)** — initial design.

## Problem

Anything that wants to react to what a Claude Code instance is doing —
a chat bot, an IDE integration, a multiplexer dashboard, a CI watcher,
a personal scratch tool — typically reaches for one of two
unsatisfying mechanisms:

1. **Polling the pane**. Tail `tmux capture-pane` output (or the
   equivalent in another terminal) every few hundred milliseconds and
   classify the on-screen TUI into states like Working / Idle /
   Blocked. Every Claude Code release that retouches the UI risks
   silently breaking the classifier.
2. **Synthesizing keystrokes**. To answer a permission prompt or pick
   an `AskUserQuestion` option, send arrow keys and Enter into the
   pane. Brittle in pane geometry, race-prone, and doesn't compose
   with users who are also at the keyboard.

Claude Code as of `2.1.133` exposes a hook surface that, taken
together, covers most of what these consumers actually want:
`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
`Notification`, `Stop`, `SessionEnd`, and — critically — the
synchronous `PermissionRequest` hook that intercepts permission
decisions, plan-mode confirmations, and `AskUserQuestion` pickers
before the TUI does. Empirically verified 2026-05-08 (see
[Background](#background-empirical-test-of-permissionrequest)).

`claude-tap` is the small, standalone package that wraps Claude Code
once and turns these hooks into a stable, consumer-agnostic event
stream plus a synchronous decision bridge. It has no opinions about
who consumes the stream — that is a separate layer.

## Goals

1. Wrap any `claude` invocation that opts in (via env gate) and inject
   the full set of Claude Code hooks transparently.
2. Emit a structured, append-only event stream covering every hook fire,
   correlated with the originating session (Claude session UUID), tmux
   surface (session/window/pane), and an optional consumer-defined
   `surface_id`.
3. Provide a synchronous decision bridge for `PermissionRequest`
   specifically, so a remote consumer (Telegram bot, chat backend, GUI)
   can answer permission prompts without `send-keys`.
4. **Fail open**. Whenever any subsystem of claude-tap is unreachable
   (no listener bound, listener crashes, file write fails, timeout
   elapses), Claude must continue with its native default — typically
   the in-pane TUI prompt — and the local user retains full control.
5. **Tmux first-class but not required**. When `$TMUX_PANE` is set,
   capture rich tmux context automatically. Outside tmux, events still
   work with `"tmux": null`.
6. **Consumer-agnostic**. claude-tap has no knowledge of any specific
   downstream — no chat-bot abstractions, no GUI hooks, no embedded
   "modes". It is one shape (event stream + decision socket) that any
   consumer can adapt to.

## Non-goals (v0.1)

- macOS / Linux native desktop notification integration.
- Non-Claude-Code agents (Codex, Cursor, Gemini, OpenCode, etc.).
  These are explicitly out of scope; if needed later, a separate
  package per agent (`codex-tap`, ...) is the right shape, not a
  generic AgentHookDef framework like cmux's.

## Background: empirical test of PermissionRequest

On 2026-05-08 we registered a `PermissionRequest` hook that read its
stdin into a log, slept 30 seconds, and returned `{}` (empty). We then
launched a real `claude` process via tmux and triggered tool calls.
Findings:

| Scenario | Observation |
|---|---|
| Default mode, `Bash(rm -f /tmp/foo)` | Hook fired immediately with full structured stdin (`tool_name`, `tool_input`, `permission_suggestions`, `permission_mode`, `session_id`, `transcript_path`, `cwd`). **TUI prompt also rendered immediately**, in parallel with the hook. After hook returned `{}`, TUI was still up waiting for user input. |
| `--dangerously-skip-permissions`, `Bash(...)` | Hook **did not fire** (claude auto-allowed). |
| `--dangerously-skip-permissions`, `AskUserQuestion(question, options[A,B,C,D])` | Hook **did fire**, with full `tool_input.questions[0].options[]` structure. `permission_mode` field reads `"bypassPermissions"`. TUI multiple-choice picker rendered in parallel. |

This rules out the "hook completely replaces the TUI" failure mode
that would have broken the local-user fallback. Claude's PermissionRequest
hook is **additive**: it runs alongside the TUI prompt, and returning
`{}` means "no opinion, fall through to default behavior" — which is
the TUI. This is the property the entire claude-tap design relies on.

## Architecture

### Components

```
~/.claude-tap/                   # all runtime state lives here
├── bin/claude                   # bash wrapper, ~80 lines, injected ahead of real claude
├── events.jsonl                 # append-only event log, schema_version=1
├── decision.sock                # unix socket, bound by whoever wants to be decision authority
└── drift.log                    # schema-drift warnings (one line per unique anomaly per process)
```

The Python package itself (CLI, hook entry point, helpers) lives in
the uv tool directory after `uv tool install claude-tap` — never under
`~/.claude-tap/`.

### Data flow

```
caller ──→ bin/claude wrapper ──→ CLAUDE_TAP_ACTIVE=1 ?
                                    │
                                    ├─ no  → exec real claude (passthrough, no injection)
                                    │
                                    └─ yes → detect tmux ($TMUX_PANE)
                                            inject --session-id <uuid>
                                            inject --settings <8-hook JSON>
                                            exec real claude
                                                │
                                                ▼
                                  claude fires a hook (any of 8)
                                                │
                                                ▼
                                  claude-tap-hook <event_name>
                                                │
                                                ▼
                                  drift.check (best-effort schema validation;
                                  appends to drift.log on first sighting of
                                  each unknown / missing field)
                                                │
                                  ┌─────────────┴──────────────┐
                                  │                            │
                          append events.jsonl       PermissionRequest only:
                          (always, synchronous)     connect decision.sock
                                                              │
                                                ┌─────────────┴────────────┐
                                                │                          │
                                       listener bound:               not bound:
                                       send request, wait,           return {} immediately
                                       relay decision JSON
                                                │                          │
                                                ▼                          ▼
                                       claude uses listener's     claude falls through
                                       decision; TUI dismisses    to TUI prompt; local
                                                                  user answers
```

### Three roles

| Role | Implemented by | Responsibility |
|---|---|---|
| **Producer** (wrapper + hook process) | claude-tap | Inject hooks, write `events.jsonl`, attempt socket on `PermissionRequest`. |
| **Event consumer** | Any process tailing `events.jsonl` (v0.1: 100 ms poll; v0.2 may switch to inotify push) | Subscribe and react to events. Multiple concurrent consumers are fine; each tails independently. |
| **Decision listener** | One process bound to `decision.sock` at a time | Answer `PermissionRequest` events. Optional. |

### Invariants

1. `events.jsonl` is always written before any socket attempt. The hook
   never depends on socket availability for the durable side of its job.
2. `decision.sock` returning nothing (no listener / timeout / error) is
   indistinguishable to claude from "hook returned `{}`" — fail-open to
   TUI.
3. All sessions share one `events.jsonl` and one `decision.sock`.
   Routing is by `session_id` + (optionally) `tmux.window_id` /
   `surface_id` in the event payload.
4. The wrapper is a no-op when `CLAUDE_TAP_ACTIVE` is unset. Putting
   `~/.claude-tap/bin/` on `PATH` system-wide does not affect `claude`
   invocations that don't opt in.

## Event schema

### Common envelope

Every line in `events.jsonl` is one self-contained JSON object
terminated by `\n`. Lines must stay under 4 KB to preserve `O_APPEND`
single-write atomicity (POSIX guarantee for writes ≤ `PIPE_BUF`).

```json
{
  "schema_version": 1,
  "event_type": "<see table below>",
  "timestamp": "2026-05-08T10:34:52.627123+00:00",
  "claude": {
    "session_id": "ab7f420b-d0f4-4785-b4cf-5e48d8fba76e",
    "transcript_path": "/mnt/nfs/.../ab7f420b-....jsonl",
    "cwd": "/mnt/beegfs/...",
    "permission_mode": "default"
  },
  "tmux": {
    "session_name": "work",
    "window_id": "@7",
    "pane_id": "%12"
  },
  "surface_id": "",
  "payload": {}
}
```

- `tmux` is `null` when the wrapper did not detect `$TMUX_PANE`.
- `surface_id` is the empty string when `CLAUDE_TAP_SURFACE_ID` is not
  set by the consumer.
- `schema_version` is `1`. Backward-incompatible changes bump this and
  rev the spec.

### Event types and payloads

| `event_type` | Triggered by | `payload` shape |
|---|---|---|
| `session_start` | `SessionStart` hook | `{}` (`startupReason` will appear here in v0.2 if Claude Code adds it back) |
| `user_prompt_submit` | `UserPromptSubmit` hook | `{"prompt": "..."}` |
| `pre_tool_use` | `PreToolUse` hook | `{"tool_name": "Bash", "tool_input": {...}}` |
| `post_tool_use` | `PostToolUse` hook | `{"tool_name": "Bash", "tool_input": {...}, "tool_response": {...}}` |
| `notification` | `Notification` hook | `{"message": "..."}` |
| `stop` | `Stop` hook | `{}` |
| `session_end` | `SessionEnd` hook | `{"reason": "..."}` |
| `permission_request` | `PermissionRequest` hook | `{"request_id": "uuid", "tool_name": "...", "tool_input": {...}, "permission_suggestions": [...]}` |

`request_id` on `permission_request` is generated by the hook process,
written to `events.jsonl`, and used as the correlation key on
`decision.sock`.

### Out of claude-tap's scope: transcript content

Claude's actual message content (assistant text, thinking blocks,
tool_use blocks) lives in the JSONL referenced by `transcript_path`.
claude-tap exposes `transcript_path` in every event but does not parse
or stream that file. Consumers that need streaming message text tail
it directly. Keeping these two layers independent means a Claude Code
release that changes transcript schema does not break claude-tap.

## Decision socket protocol

### Wire format

`decision.sock` is a Unix stream socket. One TCP-style connection per
hook invocation. Newline-delimited JSON, exactly one round-trip, then
the hook closes the connection.

**Hook → listener** (sent immediately on connect):

```json
{"request_id":"r-abc","session_id":"ab7f420b-...","tool_name":"Bash","tool_input":{"command":"rm -rf /tmp/foo"},"permission_suggestions":[{"type":"addDirectories","directories":["/tmp"],"destination":"session"}]}\n
```

**Listener → hook** (within `CLAUDE_TAP_DECISION_TIMEOUT`, default 120s):

```json
{"request_id":"r-abc","decision":<opaque pass-through>}\n
```

The hook then writes the value of `decision` (raw, unmodified) to its
stdout and exits 0. The hook does **not** validate or interpret the
shape of `decision` — that contract is between the listener and Claude
Code itself.

### Why decision is opaque

Claude Code's hook decision JSON shape may evolve across versions
(today, verified 2026-05-08:
`{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}`).
Keeping claude-tap agnostic means a Claude Code update only requires
listeners to update their decision constructors; claude-tap stays put.

### Concurrency

Multiple sessions can have concurrent `PermissionRequest` hooks in
flight. Each is its own socket connection. The listener is a standard
multi-client async server: it accepts each connection, reads the
request, performs whatever decision logic it implements (which may
involve waiting on user input, network I/O, or anything else), and
writes the response. `request_id` makes responses unambiguous even if
the listener processes them out of order.

### Single-listener constraint

A Unix stream socket has exactly one server. Only one process can be
the decision authority at a time. This is **a feature**: there is no
sensible behavior for two processes simultaneously claiming "I'll
answer Claude's permission prompts". A consumer that wants fan-out
(e.g., "send to Telegram and to a desktop GUI, take whichever
responds first") implements that internally.

## Wrapper script

`~/.claude-tap/bin/claude` is a bash script (~80 lines). Behavior:

1. Resolve the real `claude` binary by walking `$PATH`, skipping the
   directory containing the wrapper (so we never recurse).
2. Pass through unchanged if `CLAUDE_TAP_ACTIVE` is not `1`, or if
   `CLAUDE_TAP_DISABLED` is `1`. The wrapper is a no-op outside
   opt-in environments.
3. Pass through subcommands that don't accept `--session-id` or
   `--settings` (`mcp`, `config`, `api-key`, `--version`, `-v`).
4. Detect tmux: when `$TMUX_PANE` is set, run `tmux display-message
   -t "$TMUX_PANE" -p '#{session_name}|#{window_id}'` and export the
   results plus `$TMUX_PANE` as `CLAUDE_TAP_TMUX_SESSION_NAME`,
   `CLAUDE_TAP_TMUX_WINDOW_ID`, `CLAUDE_TAP_TMUX_PANE_ID` for the
   hook process to read.
5. Generate a fresh UUID for `--session-id <uuid>` unless the user
   already passed `--session-id`, `--resume`, or `--continue`.
6. Build the inline `--settings` JSON, registering all eight hooks
   pointing at `claude-tap-hook <event_name>`.
7. `exec` the real claude with the injected flags appended at the
   front of the user's argv.

The full script body is rendered at install time by `claude-tap install`
from a Python-resourced template, so version bumps are picked up
automatically.

### Settings JSON template

The wrapper writes (single line in practice):

```json
{
  "hooks": {
    "SessionStart":      [{"matcher":"","hooks":[{"type":"command","command":"claude-tap-hook SessionStart","timeout":10}]}],
    "UserPromptSubmit":  [{"matcher":"","hooks":[{"type":"command","command":"claude-tap-hook UserPromptSubmit","timeout":10}]}],
    "PreToolUse":        [{"matcher":"","hooks":[{"type":"command","command":"claude-tap-hook PreToolUse","timeout":10}]}],
    "PostToolUse":       [{"matcher":"","hooks":[{"type":"command","command":"claude-tap-hook PostToolUse","timeout":10}]}],
    "Notification":      [{"matcher":"","hooks":[{"type":"command","command":"claude-tap-hook Notification","timeout":10}]}],
    "Stop":              [{"matcher":"","hooks":[{"type":"command","command":"claude-tap-hook Stop","timeout":10}]}],
    "SessionEnd":        [{"matcher":"","hooks":[{"type":"command","command":"claude-tap-hook SessionEnd","timeout":2}]}],
    "PermissionRequest": [{"matcher":"","hooks":[{"type":"command","command":"claude-tap-hook PermissionRequest","timeout":125}]}]
  }
}
```

The 125s `PermissionRequest` timeout is intentionally 5s longer than
claude-tap's own internal socket timeout (default 120s) so the hook
process gets a clean cleanup window. See [Failure modes](#failure-modes).

`SessionEnd` timeout of 2s is short on purpose: it fires while claude
is shutting down and shouldn't delay exit.

## Hook process

`claude-tap-hook <event_name>` is a small Python entry point (~150
lines). One invocation per hook fire.

```python
def main(event_name: str) -> int:
    raw = json.load(sys.stdin)

    event = build_event(
        event_type=normalize_event_name(event_name),
        claude=ClaudeInfo.from_hook_payload(raw),
        tmux=read_tmux_from_env(),
        surface_id=os.environ.get("CLAUDE_TAP_SURFACE_ID", ""),
        payload=extract_payload(event_name, raw),
    )

    append_jsonl_atomic(claude_tap_dir() / "events.jsonl", event)

    if event_name == "PermissionRequest":
        request_id = generate_request_id()
        event["payload"]["request_id"] = request_id
        decision = try_socket_decision(
            sock_path=claude_tap_dir() / "decision.sock",
            request=event["payload"],
            timeout=float(os.environ.get("CLAUDE_TAP_DECISION_TIMEOUT", "120")),
        )
        json.dump(decision or {}, sys.stdout)
    else:
        sys.stdout.write("{}")
    return 0
```

`try_socket_decision` returns `None` on **any** failure (file not
found, connection refused, timeout, malformed response, mismatched
request_id, OS error). The caller substitutes `{}` and exits cleanly.

### Hook return value semantics

This is load-bearing for the entire design — explicitly tabulated to
prevent future readers from mistaking timeout for deny:

| Hook stdout | Meaning to Claude | Effect |
|---|---|---|
| `{}` | "I have no opinion." | Fall through to default behavior. For `PermissionRequest`, default = TUI prompt; local user answers. For non-permission hooks, no effect. |
| `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}` | "Approve as the user." | Claude proceeds; TUI shows `Allowed by PermissionRequest hook`. |
| `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"deny"}}}` | "Reject as the user." | Claude denies; TUI shows `Denied by PermissionRequest hook`. |

> Verified empirically against Claude Code 2.1.133 on 2026-05-08. Note
> that `PermissionRequest` uses `decision.behavior`, not the
> `permissionDecision` field that `PreToolUse` uses. They are different
> formats; do not cross them.

Empty `{}` is therefore **always safe**. The TUI remains, the local
user keeps full control. No code path in claude-tap returns "deny" on
behalf of the user.

## Schema drift detection

`claude_tap.drift` runs on every hook invocation, before payload
extraction. It validates the Claude Code stdin against an expected
per-event field set, hand-maintained in `drift.py:_EXPECTED`, and
appends one line to `~/.claude-tap/drift.log` on the **first sighting
per process** of each anomaly:

| Kind | Meaning |
|---|---|
| `MISSING` | A required field is absent — extraction will produce empty values. Update `_EXPECTED` and the relevant extractor in `hook.py`. |
| `UNKNOWN` | A top-level field we have no entry for is present — Claude added something. Informational. |
| `UNKNOWN_EVENT` | The hook fired with an event_name we don't have a schema for at all. |

Drift checking is best-effort: any error inside the module is
swallowed so a buggy schema entry can never break the hook. After a
Claude Code release, `grep MISSING ~/.claude-tap/drift.log` is the
fastest way to spot contract changes that need our attention. Each
record is dedupped per (event, kind, field) within a single hook
process lifetime, so a stable drift never floods the file.

This module replaces the role that `drift.log` plays in
[`claude-code-state`](https://github.com/wuwenrui555/claude-code-state)
for screen-text drift, but at a different layer: claude-tap watches
the *contract* (hook stdin schema), claude-code-state watches *visual
output*. The two are complementary signal sources; together they make
"Claude Code released a breaking change" loud and obvious.

When drift fires, the maintenance loop is documented in
[`docs/verifying-hook-contract.md`](../../verifying-hook-contract.md):
pull the official Claude Code hook docs, diff against `drift.py` /
`hook.py`, smoke-test ambiguous stdout shapes against real claude in
an isolated tmux session, and propagate the verified result into
code + tests + this spec.

## CLI surface

The Python package installs **two** console_scripts:

- `claude-tap` — user-facing CLI (subcommands below).
- `claude-tap-hook` — the hook entry point invoked from the wrapper's
  injected settings JSON. Not a user-facing command; users never run
  it directly. Splitting it from `claude-tap` keeps the hot-path fork
  cheap (no top-level subcommand parsing) and keeps the settings JSON
  short.

| Subcommand | Purpose |
|---|---|
| `claude-tap install` | Write `~/.claude-tap/bin/claude` (chmod +x). Idempotent. |
| `claude-tap uninstall` | Remove `~/.claude-tap/bin/`. Leaves `events.jsonl` alone (audit trail). |
| `claude-tap watch` | Subscribe to `events.jsonl` (v0.1: 100 ms poll; pretty-printed one line per event). |
| `claude-tap watch --json` | Same, but raw `events.jsonl` lines (pipe-friendly). |
| `claude-tap bridge --stdio` | Bind `decision.sock`. For each request, print to stdout and read decision from stdin. Manual decision authority for testing. |
| `claude-tap bridge --auto allow` | Bind `decision.sock`, automatically allow every request. **Testing only.** |
| `claude-tap version` | Print package version. |

## Python API

For consumers integrating in-process. Exposed at top level of the
`claude_tap` package.

```python
from claude_tap import EventStream, DecisionListener

# Event subscription. v0.1 polls at 100 ms; presents as async iterator.
async for event in EventStream():
    handle(event)

# Decision authority. Bind the socket; respond per-request.
async with DecisionListener() as listener:
    async for req in listener:
        decision = await your_logic(req)
        await listener.respond(req.request_id, decision)
```

Consumers that just want to tail the file can also do so directly with
`tail -f` or any file-watching mechanism; the API helpers are
ergonomic, not required.

## Configuration

### Environment variables

| Variable | Default | Set by | Purpose |
|---|---|---|---|
| `CLAUDE_TAP_ACTIVE` | unset | user / consumer | `=1` activates the wrapper. Otherwise passthrough. |
| `CLAUDE_TAP_DISABLED` | unset | user | `=1` forces passthrough even if `_ACTIVE=1`. Escape hatch. |
| `CLAUDE_TAP_DIR` | `~/.claude-tap` | user | Root for wrapper, events file, socket. |
| `CLAUDE_TAP_SURFACE_ID` | `""` | consumer | Opaque consumer-defined routing label. Pass-through, not interpreted. |
| `CLAUDE_TAP_DECISION_TIMEOUT` | `120` | user | Socket round-trip timeout in seconds. |
| `CLAUDE_TAP_TMUX_SESSION_NAME` | (auto) | wrapper writes, hook reads | Internal contract. |
| `CLAUDE_TAP_TMUX_WINDOW_ID` | (auto) | wrapper writes, hook reads | Internal contract. |
| `CLAUDE_TAP_TMUX_PANE_ID` | (auto) | wrapper writes, hook reads | Internal contract. |

### Paths

```
$CLAUDE_TAP_DIR/                # default ~/.claude-tap/
├── bin/claude                  # wrapper, mode 0755
├── events.jsonl                # append-only, mode 0644
└── decision.sock               # unix socket; mode is the listener's choice
```

`decision.sock` is created by whichever process binds it as a server
(the decision listener). claude-tap does not dictate its mode — a
listener that wants to share access with another user can chmod it
itself.

The Python code itself (CLI entry points, hook executable, helpers)
lives in the uv tool's managed venv, not under `$CLAUDE_TAP_DIR`.

## Failure modes

| Scenario | Behavior |
|---|---|
| `events.jsonl` write fails (disk full, permission, FS error) | Log warning to stderr (Claude shows it). Continue: hook returns `{}` so claude is not blocked. |
| `decision.sock` does not exist | hook returns `{}` immediately. TUI takes over. |
| `decision.sock` exists but connection refused | Same: `{}`, TUI fallback. |
| Listener bound but takes longer than `CLAUDE_TAP_DECISION_TIMEOUT` | hook closes its socket end (listener sees connection close → can clean up its UI). hook returns `{}`. TUI fallback. |
| Listener returns malformed JSON or wrong `request_id` | hook returns `{}`. TUI fallback. |
| `uuidgen` unavailable in `$PATH` | Skip `--session-id` injection; let claude generate its own. Other functionality unaffected. |
| Not in tmux (`$TMUX_PANE` unset) | `tmux: null` in events. All other behavior unchanged. |
| Real `claude` binary not found in `$PATH` (skipping wrapper dir) | Wrapper exits 127 with stderr message. Same as a missing `claude`. |
| User passes their own `--session-id` / `--resume` / `--continue` | Skip wrapper's `--session-id` injection. `--settings` injection still happens. |

**Overall principle**: any subsystem of claude-tap that fails degrades
to "claude behaves as if claude-tap were not installed". `{}` is the
universal recovery value, and `{}` always means TUI fallback (never
deny).

## Testing strategy

### Unit tests

- Event envelope serialization round-trip (ensure ASCII-safe JSON,
  stable key ordering, `\n` termination).
- `event_name` normalization (`SessionStart` ↔ `session_start`).
- `permission_suggestions` pass-through (must survive verbatim).
- Socket wire encoding/decoding (newline-delimited, partial reads).
- `try_socket_decision` failure-mode matrix (each entry in the
  Failure modes table).

### Integration tests (no real claude)

- Spawn `claude-tap-hook PermissionRequest` as a subprocess. Pipe
  fixture stdin. Assert events.jsonl line and stdout `{}` when no
  listener bound.
- Same, with a fake listener bound to `decision.sock`. Assert the
  request arrives, the response is relayed to hook stdout.
- Concurrent: spawn N hook processes, fake listener responds to each
  with distinct `request_id`. Assert no cross-talk.

### Integration tests (real claude)

- Spawn real claude in a tmux test session with isolated
  `CLAUDE_CONFIG_DIR` and `CLAUDE_TAP_DIR`. Send a prompt that triggers
  a Bash tool call. Assert correct sequence of events in `events.jsonl`
  (`session_start`, `user_prompt_submit`, `pre_tool_use`,
  `permission_request`, `post_tool_use`, `stop`).
- Bypass mode: same with `--dangerously-skip-permissions` and an
  `AskUserQuestion`-triggering prompt. Assert `permission_request` does
  fire for AskUserQuestion (matches the empirical test).

### Manual smoke

Three terminals:

1. `claude-tap watch` — observe event stream.
2. `claude-tap bridge --stdio` — be the decision authority.
3. `CLAUDE_TAP_ACTIVE=1 ~/.claude-tap/bin/claude` — drive claude
   normally. Issue tool calls that need permission.

Both `watch` and `bridge` should react in real time. Killing `bridge`
mid-session should cause subsequent permission prompts to fall back
to the TUI.

## Sample consumer

This file is shipped at `examples/sample_consumer.py` in the repo and
is intentionally short and complete. It is the canonical reference
for "how do I write a claude-tap consumer" — copy and replace the
stdin prompt with your actual decision UI (chat-platform message,
GUI dialog, LLM judge, ...).

```python
#!/usr/bin/env python3
"""Reference consumer for claude-tap.

Subscribes to events (via EventStream — looks like push, no manual
tail) and binds decision.sock to answer PermissionRequests
interactively from stdin.

Copy this file as a starting point for your own consumer
(Telegram bot, chat backend, GUI, ...).
"""

import asyncio
import json
import sys

from claude_tap import EventStream, DecisionListener


async def watch_events() -> None:
    """Print every event as it arrives.

    EventStream is an async iterator. v0.1 polls events.jsonl at 100 ms
    intervals; latency is bounded by that interval. v0.2 may switch to
    inotify-backed push. From the consumer's perspective, it is always
    just `async for`.
    """
    async for event in EventStream():
        et = event["event_type"]
        sid = event["claude"]["session_id"][:8]
        tmux = event.get("tmux") or {}
        win = tmux.get("window_id", "-")
        payload = event.get("payload", {})

        match et:
            case "user_prompt_submit":
                summary = payload.get("prompt", "")[:60]
            case "pre_tool_use" | "post_tool_use":
                summary = (
                    f'{payload.get("tool_name")}'
                    f'({json.dumps(payload.get("tool_input", {}), ensure_ascii=False)[:50]})'
                )
            case "permission_request":
                summary = f'⚠️  {payload.get("tool_name")} needs decision'
            case _:
                summary = ""

        print(f"[{et:<22}] sess={sid} win={win:>4} {summary}")


async def serve_decisions() -> None:
    """For each PermissionRequest, prompt operator via stdin.

    A real consumer would route to Telegram / a GUI / an LLM judge
    instead of blocking on stdin.
    """
    async with DecisionListener() as listener:
        async for req in listener:
            print(f"\n>>> Decision needed")
            print(f"    session   = {req.session_id}")
            print(f"    tool      = {req.tool_name}")
            print(
                f"    input     = "
                f"{json.dumps(req.tool_input, ensure_ascii=False, indent=2)}"
            )
            for s in req.permission_suggestions or []:
                print(f"    suggestion= {s}")
            print(
                f"    type 'allow' or 'deny' (default deny): ",
                end="",
                flush=True,
            )

            answer = (await asyncio.to_thread(sys.stdin.readline)).strip().lower()
            behavior = "allow" if answer == "allow" else "deny"

            # PermissionRequest expects decision.behavior (NOT
            # permissionDecision — that is the PreToolUse format).
            decision = {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {"behavior": behavior},
                }
            }
            await listener.respond(req.request_id, decision)
            print(f"<<< sent: {behavior}\n")


async def main() -> None:
    await asyncio.gather(watch_events(), serve_decisions())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
```

## Resolved verifications

1. **`PermissionRequest` decision JSON shape** — verified empirically
   on 2026-05-08 against Claude Code 2.1.133. The correct format is:

   ```json
   {"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}
   ```

   `decision.behavior` (NOT `permissionDecision`, which is the
   `PreToolUse` field). When allowed, TUI shows
   `Allowed by PermissionRequest hook`; when denied,
   `Denied by PermissionRequest hook` plus
   `Error: Permission denied by hook`. TUI never prompts the user when
   the hook returns one of these. Empty `{}` falls through to the TUI
   prompt as expected.

   The sample consumer and the "hook return value semantics" table use
   this verified format. Since claude-tap itself treats `decision` as
   opaque bytes, only consumer-side code needs to change if Claude
   Code revises the format in a future release.

## Decisions log

For posterity, the design choices that distinguish this version from
plausible alternatives:

- **IPC model**: parallel file (`events.jsonl`) + opt-in unix socket
  (`decision.sock`). Daemonless. Chosen over "all socket" (cmux
  pattern) to keep `events.jsonl` durable independent of any listener
  process, and over "all file" to keep `PermissionRequest` synchronous
  without polling.
- **Activation gating**: env-gated (`CLAUDE_TAP_ACTIVE=1`). Chosen over
  "always active when on PATH" so that putting `~/.claude-tap/bin/`
  ahead of real claude on a global PATH is safe.
- **Multi-session**: single shared `events.jsonl` + single shared
  `decision.sock`. Routing by `session_id` (and optionally
  `tmux.window_id` / `surface_id`) inside the event/request payload.
- **Tmux**: first-class auto-detection in the wrapper, three fields
  (`session_name`, `window_id`, `pane_id`).
- **`surface_id`**: optional consumer-defined override, opaque to
  claude-tap. Default empty. Useful for consumers running in
  environments without tmux.
- **Timeout values**: socket 120s, Claude hook 125s. 5s margin lets
  the hook process clean up cleanly before claude's outer SIGKILL.
- **Fail-open semantics**: every claude-tap failure path returns `{}`,
  which means "no opinion → TUI fallback → local user keeps control".
  Never deny on the user's behalf.
