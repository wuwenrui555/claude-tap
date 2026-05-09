# Verifying the Claude Code hook contract

> Maintenance runbook. Use this after a Claude Code release, when adding
> a new hook event to claude-tap, or when `~/.claude-tap/drift.log`
> shows new lines you don't recognize.

claude-tap depends on the exact stdin/stdout JSON shape Claude Code
uses for each hook. That contract changes occasionally — fields get
renamed, new optional fields appear, decision-output formats evolve.
This document walks the loop we use to keep the contract pinned.

A worked example from the day this doc was written lives in
[Case study: 2026-05-08](#case-study-2026-05-08--when-the-docs-were-wrong)
at the bottom — a real run where the official docs disagreed with
the running Claude Code in two places, and the drift detector caught
it on first live use.

## The four-step loop

```text
   ┌─→ 1. Pull the official docs
   │
   ├─→ 2. Diff against claude-tap's schema (drift.py + hook.py)
   │
   ├─→ 3. Smoke-test ambiguous control fields against real claude
   │
   └─→ 4. Update code + tests + spec, commit
```

## Step 1: Pull the official docs

The Claude Code hook docs live at:

```text
https://code.claude.com/docs/en/hooks
```

(Note: `https://docs.claude.com/en/docs/claude-code/hooks` redirects
here as of 2026-05-08. If a future redirect points elsewhere, follow
it.)

The page documents every hook event with three blocks each:

- **stdin schema** — fields Claude sends INTO the hook
- **stdout schema** — what the hook can print back to control Claude
- **special semantics** — block-vs-non-blocking, exit-code meanings,
  matcher syntax, timeout defaults

For an interactive read, fetch with WebFetch and extract specifically:

```text
"List EVERY hook event documented. For each, quote the stdin JSON
schema, the stdout JSON schema, and any special control fields like
`continue`, `decision`, `permissionDecision`, `hookSpecificOutput`,
`updatedInput`. Note matcher syntax and timeout defaults."
```

Save the full pull as a dated artifact if you need to reference it
later — e.g., `docs/snapshots/hooks-2026-05-08.md`. (Not required for
the loop itself; the docs page is canonical.)

## Step 2: Diff against claude-tap's schema

Two places encode our understanding of the hook contract:

| File | What it claims |
|---|---|
| `src/claude_tap/drift.py` (`_EXPECTED`) | Per-event sets of required + optional stdin field names |
| `src/claude_tap/hook.py` (`extract_payload`) | Per-event field-by-field extraction into our payload |

Walk every event we support (8 in v0.1: `SessionStart`,
`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Notification`,
`Stop`, `SessionEnd`, `PermissionRequest`) and ask three questions
about each:

1. **Required fields**: every required field in docs is also in our
   `required` set, and we extract it in `hook.py`.
2. **Optional fields**: every optional field in docs is at least in
   our `optional` set, so drift won't flag it as `UNKNOWN`.
3. **Phantom fields**: no field name we hardcode in `extract_payload`
   is absent from docs — otherwise we're reading something that
   doesn't exist.

But — and this is the entire point of the rest of this document —
**docs may be wrong**. Don't commit a schema change motivated purely
by docs without confirming the field actually appears in real
hook stdin (Step 3 below).

## Step 3: Smoke-test ambiguous stdout control fields

Stdin field names (Step 2) are easy: they are either there or not.
**Stdout decision formats are subtler** — multiple events look
similar but aren't interchangeable. The 2026-05-08 example:

- `PreToolUse` uses `hookSpecificOutput.permissionDecision: "allow"`
- `PermissionRequest` uses `hookSpecificOutput.decision.behavior: "allow"`

Mixing them silently fails: hook returns the wrong shape, Claude
treats it as `{}` (no opinion), TUI prompts the user as though no
hook fired. **No error is emitted** — you have to observe whether
the TUI got dismissed.

The verification recipe:

### 3a. Set up an isolated fixture hook

```bash
mkdir -p /tmp/cc-hook-probe
cat > /tmp/cc-hook-probe/hook.sh <<'EOF'
#!/bin/bash
# Log what Claude sent us so we can inspect the input shape too.
{
  printf '==== %s HOOK FIRED ====\n' "$(date '+%H:%M:%S.%3N')"
  echo "stdin:"; cat; echo
} >> /tmp/cc-hook-probe/hook.log
# Print the stdout shape we are testing.
echo '{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}'
EOF
chmod +x /tmp/cc-hook-probe/hook.sh
```

### 3b. Spawn real claude in a tmux session with the fixture hook injected

We spawn a *separate* claude (not the one you're chatting with) so
this never affects your live session.

```bash
SETTINGS='{"hooks":{"PermissionRequest":[{"matcher":"","hooks":[{"type":"command","command":"/tmp/cc-hook-probe/hook.sh","timeout":35}]}]}}'

tmux new-session -d -s probe -x 220 -y 60
# unset CLAUDECODE so claude does not refuse to nest, and use absolute
# path to bypass any user `claude=` alias that adds --dangerously-skip-permissions.
tmux send-keys -t probe \
  "unset CLAUDECODE; $(whence -p claude) --settings '$SETTINGS'" \
  C-m
sleep 12  # let claude paint
```

### 3c. Trigger the event you're testing and capture the pane

```bash
# A Bash command that probably needs permission (touch in /var/tmp)
tmux send-keys -t probe "请用 Bash 工具执行: touch /var/tmp/probe-$(date +%s)" C-m
sleep 8
tmux capture-pane -t probe -p
cat /tmp/cc-hook-probe/hook.log
```

### 3d. Read the verdict from the pane, not from claude's text

The literal strings you're looking for in the pane:

| Hook returned | What you should see in the pane |
|---|---|
| Correct allow shape | `⎿  Allowed by PermissionRequest hook` then tool output |
| Correct deny shape | `⎿  Error: Permission denied by hook` and `⎿  Denied by PermissionRequest hook` |
| **Wrong shape** (or `{}`) | `Do you want to proceed? ❯ 1. Yes / 2. ... / 3. No` — TUI took over because Claude treated our response as no-opinion |

Side-effect check (the second-best evidence):

- For `allow`: did the tool actually run? `ls /var/tmp/probe-*`
- For `deny`: did the tool NOT run?

If TUI took over despite a non-empty hook stdout, the format is
wrong. Edit `/tmp/cc-hook-probe/hook.sh`, restart claude in the tmux
session, retry.

### 3e. Tear down

```bash
tmux kill-session -t probe
rm -rf /tmp/cc-hook-probe
```

## Step 4: Update code + tests + spec

Once you have the verified shapes, propagate:

| Change | File |
|---|---|
| Field-name fixes from Step 2 | `src/claude_tap/hook.py` (`extract_payload`) |
| Schema additions/renames | `src/claude_tap/drift.py` (`_EXPECTED`) |
| New decision shape from Step 3 | `src/claude_tap/cli.py` (`_build_decision`) |
| New decision shape, again | `examples/sample_consumer.py` |
| Documented behavior of return values | `docs/superpowers/specs/2026-05-08-claude-tap-design.md` (the "Hook return value semantics" table) |
| Regression tests for both bug classes | `tests/test_hook.py` (`test_extract_payload_*`) and `tests/test_drift.py` |

Run the suite:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src/ tests/ examples/
.venv/bin/ruff format --check src/ tests/ examples/
```

Commit with a message that names the verification source — and
specifically calls out **empirical** if your finding overrode docs:

```bash
git commit -m "fix(drift): SessionEnd uses 'reason' (empirical 2026-05-08; docs wrong)"
```

## When to re-run this loop

| Trigger | What to do |
|---|---|
| Claude Code minor release lands | Skim docs diff → re-run Step 2 quickly. Step 3 only if a hook semantics line changed. |
| `~/.claude-tap/drift.log` has new `MISSING` entries | Step 1 → 2 → 4. Step 3 not usually needed (input shape is unambiguous). |
| `~/.claude-tap/drift.log` has new `UNKNOWN` entries | Step 1 → decide whether to add the field. Step 4 if yes. |
| Adding a new hook event to claude-tap (one of the 21+ we don't yet support) | Full loop, Steps 1–4. |
| A consumer (chat backend, IDE plugin, dashboard, etc.) reports "the decision didn't take effect" | Step 3 — likely a stdout format mismatch. |

## Reference URLs and key files

- **Official docs (canonical)**: <https://code.claude.com/docs/en/hooks>
- **Schema we encode**: `src/claude_tap/drift.py` (`_EXPECTED`)
- **Extraction we run**: `src/claude_tap/hook.py` (`extract_payload`)
- **Decision builder**: `src/claude_tap/cli.py` (`_build_decision`)
- **Reference consumer using current shape**: `examples/sample_consumer.py`
- **Hook return semantics table**: `docs/superpowers/specs/2026-05-08-claude-tap-design.md` (search "Hook return value semantics")
- **Drift log location at runtime**: `$CLAUDE_TAP_DIR/drift.log` (default `~/.claude-tap/drift.log`)

## A note on trust

The docs are accurate at the time we read them, but they are not
infallible. Three failure modes you should expect:

1. **Documentation lag** — a Claude Code release ships before its
   docs page reflects the change. Step 3 (smoke test) is the only
   way to catch this. We hit this with `permissionDecision` vs
   `decision.behavior` (different events, different fields, both
   valid — but the docs hadn't disambiguated cleanly).
2. **Implementation lag** — docs describe a planned shape, and the
   shipped code hasn't caught up. Step 3 catches this too.
3. **Documentation just wrong** — the docs page describes fields
   that no shipped version actually uses. Step 2 (schema diff) plus
   running the drift detector against a live session catches this.

The drift detector is the long-running version of this loop: it
flags surprises in real time once Step 4 has codified our
expectations. Treat its `drift.log` as a TODO list of contract
divergences to investigate.

## Case study: 2026-05-08 — when the docs were wrong

The first live run of `claude-tap` against Claude Code 2.1.136
(the latest release at that time) produced 7 drift entries within
seconds. Three categories:

### 1. Docs/reality mismatch (the docs were wrong)

| Event | Docs claim | Reality |
|---|---|---|
| `SessionEnd` | `end_reason` (required) | `reason` |
| `Notification` | `notification_type` + `notification_message` (required) | only `message` |

We had earlier "fixed" `hook.py` and `drift.py` to follow the docs,
trusting `code.claude.com/docs/en/hooks`. The drift module on first
real run reported MISSING for the docs-named fields and UNKNOWN for
the actual fields — i.e., **our schema disagreed with reality in
both directions at once**, the cleanest possible signal that we'd
gone the wrong way.

CHANGELOG inspection confirmed there had been **no rename** between
the originally-correct names (`reason`, `message`) and the docs
claim. The docs page simply has an error; either it was written
ahead of a planned rename that never shipped, or it's a typo in the
field names. We reverted to the empirical names and added test
fixtures asserting the real schema.

### 2. Schema gaps (we were missing optional fields)

Real `PreToolUse`, `PostToolUse`, and `PermissionRequest` payloads
include `tool_use_id` (string). `PostToolUse` additionally includes
`duration_ms`. We don't extract these into our event payload, but
they are **expected** in claude's stdin, so they belong in the
`optional` set so drift doesn't keep flagging them. Added.

### 3. Confirmed correct

`PermissionRequest` `permission_suggestions` (which we already had
in optional) appeared with rich `addRules`-style content that
matches the docs. So it's not all wrong — just two specific events.

### Lesson for future maintenance

When a `_EXPECTED` change is motivated **purely by docs** (no live
observation), pair it with a smoke test against a real session
*before* committing. Better yet: run drift in a smoke session,
confirm `drift.log` stays empty, *then* trust the schema.

The empirical observation is canonical. Docs are a hypothesis.
