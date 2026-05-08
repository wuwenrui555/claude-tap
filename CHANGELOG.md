# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
