# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
