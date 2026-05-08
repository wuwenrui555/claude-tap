# claude-tap

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

Wrap Claude Code: structured event stream + synchronous decision bridge.

`claude-tap` installs a thin shell wrapper in front of `claude` (gated by
`CLAUDE_TAP_ACTIVE=1` so it is harmless when not opted in). When active,
it injects all eight Claude Code hooks (`SessionStart`,
`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Notification`, `Stop`,
`SessionEnd`, `PermissionRequest`) and routes them into:

- **`~/.claude-tap/events.jsonl`** — append-only event stream (durable,
  always written, single line per event).
- **`~/.claude-tap/decision.sock`** — optional unix socket your consumer
  binds when it wants to answer `PermissionRequest` synchronously.

Designed for any consumer that needs to know what a Claude Code session
is doing — chat backends, IDE integrations, terminal multiplexer
dashboards. claude-tap has no opinion about who consumes the stream.

## Status

v0.1 alpha.

## Install

```bash
uv tool install git+https://github.com/wuwenrui555/claude-tap.git
claude-tap install                 # writes ~/.claude-tap/bin/claude
```

## Use

In three terminals:

```bash
# 1. Subscribe to events
claude-tap watch

# 2. Bind the decision socket and answer interactively
claude-tap bridge --stdio

# 3. Drive Claude through the wrapper
CLAUDE_TAP_ACTIVE=1 ~/.claude-tap/bin/claude
```

Or use the Python API directly:

```python
from claude_tap import EventStream, DecisionListener

# Event subscription (push-style, async iterator)
async for event in EventStream():
    print(event["event_type"], event["claude"]["session_id"])

# Decision authority (bind decision.sock, answer PermissionRequests)
async with DecisionListener() as listener:
    async for req in listener:
        decision = {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "permissionDecision": "allow",
            }
        }
        await listener.respond(req.request_id, decision)
```

See [`examples/sample_consumer.py`](examples/sample_consumer.py) for a
complete reference consumer that ties both halves together.

## Failure modes

If anything in claude-tap goes wrong (no listener, listener crashes,
socket timeout, malformed response), the hook returns `{}` to Claude,
which Claude treats as "no opinion → fall through to default behavior".
For `PermissionRequest`, the default is the in-pane TUI prompt — so
the local user keeps full control. **`{}` never means "deny".**

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `CLAUDE_TAP_ACTIVE` | unset | `=1` activates the wrapper. |
| `CLAUDE_TAP_DISABLED` | unset | `=1` forces passthrough (overrides `_ACTIVE`). |
| `CLAUDE_TAP_DIR` | `~/.claude-tap` | Root for wrapper, events file, socket. |
| `CLAUDE_TAP_SURFACE_ID` | `""` | Opaque consumer-defined routing label. |
| `CLAUDE_TAP_DECISION_TIMEOUT` | `120` | Socket round-trip timeout (s). |

## Design

Full design at [`docs/superpowers/specs/2026-05-08-claude-tap-design.md`](docs/superpowers/specs/2026-05-08-claude-tap-design.md).

## License

Apache 2.0.
