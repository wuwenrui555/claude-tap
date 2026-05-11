# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-05-10

### Added

- `claude_tap.MessageStream`: an async iterator that yields
  `ClaudeMessage` records reconstructed from `events.jsonl` (as
  triggers) and Claude Code transcript JSONL (as the source of
  truth). Hides byte-offset bookkeeping and JSONL parsing from
  consumers.
- `claude_tap.ClaudeMessage`: dataclass shape mirrors the legacy
  `ccmux.api.ClaudeMessage` so consumers wired against that
  contract port with a one-line import change.
- `claude-tap watch-messages` CLI subcommand: dump derived messages
  to stdout as JSON Lines (image data base64-encoded). `--from-start`
  replays each session's transcript from the beginning.
- New modules: `models`, `transcript`, `tool_summary`, `messages`.
- `MessageStream` emits content directly from hook payloads
  whenever the payload is sufficient — `user_prompt_submit.prompt`
  for user text, `pre_tool_use.tool_input` for `tool_use` and
  `ExitPlanMode` plan, and `stop.last_assistant_message` for the
  final assistant reply. Transcript reads run in a "narrow" mode
  that emits only content the hook stream cannot produce
  (mid-turn assistant text, `tool_result` content, local-command
  output). Removes the user-visible delay where prompts and final
  replies only appeared after the next hook fired (typically
  several seconds).
- Hook-anchored scoped polling: after every message-progressing
  hook (`user_prompt_submit`, `pre_tool_use`, `post_tool_use`),
  `MessageStream` spawns a per-session polling task that re-reads
  the transcript at `poll_interval` until cancelled by the next
  hook for that session (or a 30 s safety deadline). Catches
  transcript writes whose fs flush lagged the bracketing hook
  fire — mid-turn text, late-flush final replies, etc. — within
  `poll_interval`. `stop` does not trigger polling (turn ended);
  `session_*` / `notification` / `permission_request` are not
  message-progressing.
- `claude-tap watch-messages` defaults to a fixed three-line block
  per message (separator, header, single body line) plus a blank
  line between blocks: every emission is exactly four lines. The
  body is escaped through `json.dumps` (so embedded newlines
  collapse to literal `\n`) but the outer quotes are stripped, so
  the line reads as plain prose without `"..."` wrappers. Long
  bodies are trimmed at the configured pretty width with `...`.
  Image attachments fold inline as `[+N image(s)]`. Pass `--json`
  for the previous JSONL output.
- `ClaudeMessage` carries `tmux_session_name` and `tmux_window_id`
  fields, copied from the event envelope at emit time. The
  `watch-messages` pretty header includes them as
  `[HH:MM:SS <sn><wid> sid8]` (e.g. `[14:23:45 ccmux@80 abcd1234]`)
  when present and falls back to `[HH:MM:SS sid8]` otherwise.
- `CLAUDE_TAP_PRETTY_WIDTH` setting (default 100): single visual
  cell width that controls **both** the `watch-messages` separator
  line and the body trim cap. Replaces the earlier split between
  `CLAUDE_TAP_PRETTY_BODY_WIDTH` (renamed) and the previously
  hardcoded 60-cell separator — the block reads as a unit, so
  the two never made sense to diverge. Bump it to see longer
  tool inputs and final replies; recognized via settings.env or
  shell export.
- `watch-messages` separator line now embeds the emit-time
  `HH:MM:SS.mmm` (UTC) — e.g. `── 14:23:46.241 ───…──`. Side by
  side with the message-time in the next line this surfaces any
  forwarding lag at a glance.
- `watch-messages` body trim is now **visual-cell-aware** via
  `unicodedata.east_asian_width`: CJK fullwidth chars count as 2
  cells, ASCII as 1. A Chinese-heavy message and an English-heavy
  message of the same configured `CLAUDE_TAP_PRETTY_BODY_WIDTH`
  now render at comparable visible widths. No new dependency.
- `settings.env` support. `claude_tap.config` now sources
  `KEY=value` lines from `./settings.env` (cwd) and
  `$CLAUDE_TAP_DIR/settings.env` (default `~/.claude-tap/settings.env`)
  into `os.environ` once at import. Shell exports always win over
  file values. Recognized keys: `CLAUDE_TAP_DIR`,
  `CLAUDE_TAP_SURFACE_ID`, `CLAUDE_TAP_DECISION_TIMEOUT`,
  `CLAUDE_TAP_POLL_INTERVAL`, `CLAUDE_TAP_POLL_MAX_DURATION`. See
  `examples/settings.env.example`. No new runtime dependency — a
  ~25-line parser handles `#` comments and quoted values.
- `MessageStream` and `EventStream` constructor `poll_interval`
  arguments now accept `None` (the new default) and fall back to
  `CLAUDE_TAP_POLL_INTERVAL`. `MessageStream.poll_max_duration`
  similarly falls back to `CLAUDE_TAP_POLL_MAX_DURATION`. Existing
  callers passing explicit values are unchanged.
- Spec at
  `docs/superpowers/specs/2026-05-10-claude-tap-v0.2-message-stream.md`.

### Changed

- **Charter shift.** v0.1.3 explicitly carved out transcript-reading
  as out of scope ("claude-tap is not the right layer for that").
  v0.2 deprecates that carve-out for the message-reconstruction
  case: empirical race measurement (see spec) confirmed transcript
  is reliably written 60–90 ms before the matching `pre_tool_use`
  hook fires, and 63% of pure-text assistant messages are
  transcript-only mid-turn narration the hook stream cannot
  recover. Centralising the read in claude-tap removes a
  duplicated subsystem from each downstream consumer. The
  character-level streaming non-goal still stands.
- Package description bumped to reflect the additional surface.

### Backwards compatibility

- `events.jsonl` schema unchanged.
- `EventStream`, `Event`, `ClaudeInfo`, `TmuxInfo`,
  `DecisionListener`, `DecisionRequest`, `claude-tap-hook` CLI, and
  every v0.1.x CLI subcommand are unchanged.
- A v0.1.x consumer compiled against v0.1.3 keeps working on v0.2.0
  without modification.

## [0.1.3] - 2026-05-09

### Added

- `Stop.payload` now carries `last_assistant_message`: the
  assistant's final text per turn. Empty string when stdin omits
  it.
- `PreToolUse.payload` and `PostToolUse.payload` now carry
  `tool_use_id` so the two halves of a tool call can be paired.
  `PostToolUse.payload` additionally carries `duration_ms`.
- Drift detector recognises `agent_id` and `agent_type` (subagent
  annotations on `PreToolUse` / `PostToolUse`, observed via
  `drift.log` on 2026-05-09); previously these emitted `UNKNOWN`
  warnings.
- Spec at
  `docs/superpowers/specs/2026-05-09-tap-v0.1.3-message-fields.md`.

### Changed

- Drift schema: `tool_use_id` (Pre/PostToolUse), `duration_ms`
  (PostToolUse), and `last_assistant_message` (Stop) are now
  marked **required** so a future Claude Code release dropping
  them triggers an alert. The wrapper still produces a valid
  event with empty defaults if a field is missing — the required
  classification only changes drift logging, not extraction.

## [0.1.2] - 2026-05-08

### Added

- Standard dev-tooling layer: `.pre-commit-config.yaml` (ruff +
  markdownlint), GitHub Actions CI workflow (pytest matrix on
  3.11 / 3.12 / 3.13 plus a pre-commit lint job), `.markdownlint.yaml`,
  `.markdownlintignore`, and an expanded `.gitignore`.
- README status badges: CI, License, Python, pre-commit.

### Changed

- `docs/verifying-hook-contract.md`: drop `ccmux-backend` as the
  named example consumer and replace it with a generic phrase, so the
  protocol stays consumer-agnostic. Tighten markdownlint compliance
  (explicit fence languages, proper heading levels for previously-bold
  subsections).

## [0.1.1] - 2026-05-08

### Added

- README "Hook-contract gaps" subsection documenting that user-initiated
  `Esc` interrupts emit no `Stop` event, and pointing consumers at pane
  parsing as the out-of-band signal that fills the gap.

### Fixed

- `claude-tap watch` previously broke its one-event-per-line invariant
  when a `user_prompt_submit` prompt contained newlines. The summary
  now passes through `json.dumps` (matching how tool inputs are
  formatted) before truncation, rendering control characters as
  literal escapes (`\n`, `\t`, etc.).

## [0.1.0] - 2026-05-08

### Added

- Initial alpha release. Wraps `claude` with a `CLAUDE_TAP_ACTIVE=1`
  shell wrapper, injects all eight Claude Code hooks, and routes them
  to `~/.claude-tap/events.jsonl` plus an optional decision socket at
  `~/.claude-tap/decision.sock`.
- Schema-drift detector that catalogs new or missing fields per hook
  to `~/.claude-tap/drift.log` for post-upgrade contract verification.
- `claude-tap` CLI with `install`, `uninstall`, `watch`, `bridge`, and
  `drift` subcommands.
