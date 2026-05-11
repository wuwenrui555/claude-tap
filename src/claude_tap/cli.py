"""claude-tap user-facing CLI."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import unicodedata
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from . import __version__, config, drift
from .config import wrapper_path
from .listener import DecisionListener
from .messages import MessageStream
from .stream import EventStream
from .wrapper import render_wrapper


def cmd_install(args) -> int:
    path = wrapper_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_wrapper())
    path.chmod(0o755)
    print(f"Wrote wrapper to {path}")
    return 0


def cmd_uninstall(args) -> int:
    path = wrapper_path()
    if path.exists():
        path.unlink()
        print(f"Removed {path}")
    parent = path.parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
    return 0


def _pretty(event: dict) -> str:
    et = event.get("event_type", "?")
    sid = event.get("claude", {}).get("session_id", "")[:8]
    tmux = event.get("tmux") or {}
    win = tmux.get("window_id", "-")
    payload = event.get("payload", {})
    if et == "user_prompt_submit":
        summary = json.dumps(payload.get("prompt", ""), ensure_ascii=False)[1:-1][:60]
    elif et in ("pre_tool_use", "post_tool_use"):
        ti = payload.get("tool_input", {})
        ti_str = json.dumps(ti, ensure_ascii=False)[:50]
        summary = f"{payload.get('tool_name')}({ti_str})"
    elif et == "permission_request":
        summary = f"⚠️  {payload.get('tool_name')} needs decision"
    else:
        summary = ""
    return f"[{et:<22}] sess={sid} win={win:>4} {summary}"


async def _watch_async(args) -> int:
    async for event in EventStream(from_start=False):
        if args.json:
            print(json.dumps(event, ensure_ascii=False))
        else:
            print(_pretty(event))
    return 0


def cmd_watch(args) -> int:
    try:
        return asyncio.run(_watch_async(args))
    except KeyboardInterrupt:
        return 0


def _message_to_jsonl(msg) -> str:
    """Render a ClaudeMessage as a single JSON line.

    Bytes in ``image_data`` are base64-encoded so the output is valid
    JSON; consumers reverse with :func:`base64.b64decode`.
    """
    d = asdict(msg)
    if d.get("image_data"):
        d["image_data"] = [
            (media_type, base64.b64encode(raw).decode("ascii"))
            for media_type, raw in d["image_data"]
        ]
    return json.dumps(d, ensure_ascii=False)


_PRETTY_TRUNCATION_MARKER = "..."


def _pretty_timestamp(ts: str | None) -> str:
    """Reduce an ISO timestamp to ``HH:MM:SS`` (UTC offset preserved)."""
    if not ts:
        return "        "
    raw = ts
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime("%H:%M:%S")
    except (ValueError, TypeError):
        # Fallback: pull out characters that look like HH:MM:SS
        return ts[11:19] if len(ts) >= 19 else ts[:8]


def _now_timestamp() -> str:
    """Wall-clock UTC ``HH:MM:SS.mmm`` for emit-side timestamping.

    UTC matches the message-side timestamps rendered by
    :func:`_pretty_timestamp`, so the side-by-side comparison
    answers "how stale is this message right now" directly.
    """
    now = datetime.now(UTC)
    return now.strftime("%H:%M:%S") + f".{now.microsecond // 1000:03d}"


def _pretty_separator() -> str:
    """Build the per-block separator with the current emit-time embedded.

    Layout: ``─ HH:MM:SS.mmm ─...─`` to a visual width matching
    ``CLAUDE_TAP_PRETTY_WIDTH``. The single-dash prefix lines the
    timestamp up at column 2, matching the ``[ `` prefix on the
    header line below it so emit-time and message-time read down
    the same column.
    """
    now = _now_timestamp()
    prefix = f"─ {now} "
    rest = max(0, config.pretty_width() - len(prefix))
    return prefix + "─" * rest


def _visual_width(s: str) -> int:
    """Approximate terminal cell width.

    East-Asian fullwidth/wide characters count as 2 cells; everything
    else (including ambiguous-width) counts as 1. Good enough for
    99% of cases; pathological cases (combining marks, ZWJ emoji
    sequences) fall back to slight visual mismatch but never crash.
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1 for ch in s)


def _visual_trim(s: str, width: int) -> str:
    """Truncate ``s`` so its visual width does not exceed ``width``."""
    if width <= 0:
        return ""
    w = 0
    out: list[str] = []
    for ch in s:
        char_w = 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
        if w + char_w > width:
            break
        out.append(ch)
        w += char_w
    return "".join(out)


def _pretty_header(msg) -> str:
    """One-line header: ``[ time tmux sid ] ROLE · content_type · tool_name``.

    Inside-bracket spaces line the message timestamp up at column 2
    so it sits directly below the emit-time embedded in the
    separator line above. The tmux fragment is
    ``<session_name><window_id>`` (e.g. ``ccmux@80``) and is omitted
    when neither field is populated.
    """
    ts = _pretty_timestamp(msg.timestamp)
    sid = (msg.session_id or "")[:8] or "--------"
    sn = msg.tmux_session_name or ""
    wid = msg.tmux_window_id or ""
    tmux_frag = f"{sn}{wid}" if (sn or wid) else ""
    parts = [(msg.role or "?").upper()]
    if msg.content_type and msg.content_type != "text":
        parts.append(msg.content_type)
    if msg.tool_name:
        parts.append(msg.tool_name)
    label = " · ".join(parts)
    head_inner = f"{ts} {tmux_frag} {sid}" if tmux_frag else f"{ts} {sid}"
    return f"[ {head_inner} ] {label}"


def _pretty_body(msg) -> str:
    """One-line body trimmed to a visual-cell width.

    The text is escaped through ``json.dumps`` (with the outer
    quotes stripped) so embedded newlines collapse to literal
    ``\\n`` and a single line is guaranteed, but the result reads
    as raw text — no surrounding ``"..."`` wrapper. The trim target
    is ``CLAUDE_TAP_PRETTY_WIDTH`` *terminal cells* (not
    codepoints): CJK fullwidth chars count as 2, ASCII as 1, so a
    block of Chinese and a block of English render at comparable
    visible widths. Image attachments are summarized inline as
    ``[+N image(s)]``.
    """
    # json.dumps yields ``"escaped string"``; the [1:-1] strips the
    # outer quotes so the body line reads as plain prose while still
    # benefiting from json's escape machinery for \n / \t / embedded
    # quotes / backslashes.
    body = json.dumps(msg.text or "", ensure_ascii=False)[1:-1]
    if msg.image_data:
        n = len(msg.image_data)
        body += f" [+{n} image{'s' if n != 1 else ''}]"
    width = config.pretty_width()
    if _visual_width(body) > width:
        keep = max(0, width - _visual_width(_PRETTY_TRUNCATION_MARKER))
        body = _visual_trim(body, keep) + _PRETTY_TRUNCATION_MARKER
    return body


def _pretty_message(msg) -> str:
    """Render a ClaudeMessage as a fixed three-line block (+blank line in caller).

    Layout::

        ── HH:MM:SS.mmm ──────────────────────  ← emit time (now, UTC)
        [HH:MM:SS sid8] ROLE · content · tool   ← msg time + ids + role
        "json-encoded body, trimmed to width…"
    """
    return "\n".join([_pretty_separator(), _pretty_header(msg), _pretty_body(msg)])


async def _watch_messages_async(args) -> int:
    async for msg in MessageStream(from_start=args.from_start):
        if args.json:
            print(_message_to_jsonl(msg), flush=True)
        else:
            print(_pretty_message(msg), flush=True)
            print(flush=True)  # blank line between blocks
    return 0


def cmd_watch_messages(args) -> int:
    try:
        return asyncio.run(_watch_messages_async(args))
    except KeyboardInterrupt:
        return 0


def _build_decision(verb: str | None) -> dict[str, Any]:
    """Build the Claude Code PermissionRequest hook stdout decision.

    Verified empirically against Claude Code 2.1.133 on 2026-05-08:
    PermissionRequest expects ``decision: {behavior: "allow"|"deny"}``
    (NOT ``permissionDecision`` — that is the PreToolUse format).
    """
    if verb == "allow":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        }
    if verb == "deny":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "deny"},
            }
        }
    return {}


async def _bridge_stdio_async(args) -> int:
    async with DecisionListener() as listener:
        print(f"[bridge] listening on {listener.path}", file=sys.stderr)
        async for req in listener:
            print(f"\n>>> {req.tool_name}", flush=True)
            print(f"    request_id = {req.request_id}", flush=True)
            print(f"    session_id = {req.session_id}", flush=True)
            print(
                f"    tool_input = {json.dumps(req.tool_input, ensure_ascii=False)}",
                flush=True,
            )
            print("    [allow / deny / <empty for {}]: ", end="", flush=True)
            answer = (await asyncio.to_thread(sys.stdin.readline)).strip().lower()
            decision = _build_decision(answer if answer in {"allow", "deny"} else None)
            await listener.respond(req.request_id, decision)
    return 0


async def _bridge_auto_async(args) -> int:
    decision = _build_decision(args.auto)
    async with DecisionListener() as listener:
        print(
            f"[bridge] auto={args.auto} listening on {listener.path}",
            file=sys.stderr,
        )
        async for req in listener:
            await listener.respond(req.request_id, decision)
    return 0


def cmd_bridge(args) -> int:
    try:
        if args.auto:
            return asyncio.run(_bridge_auto_async(args))
        return asyncio.run(_bridge_stdio_async(args))
    except KeyboardInterrupt:
        return 0


def cmd_drift(args) -> int:
    """Show schema-drift anomalies recorded in drift.log.

    By default summarizes (one line per unique anomaly + first/last
    timestamps + count). With ``--raw`` prints drift.log verbatim.
    """
    path = drift.drift_log_path()
    if not path.exists():
        print(f"No drift detected. ({path} does not exist.)")
        return 0
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        print(f"No drift detected. ({path} is empty.)")
        return 0

    if args.raw:
        sys.stdout.write(content)
        return 0

    # Summarize: dedupe by (event, kind, field), track first/last/count.
    summary: dict[tuple[str, str, str], dict[str, Any]] = {}
    for line in content.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            continue
        ts, event, kind, field = parts[0], parts[1], parts[2], parts[3]
        key = (event, kind, field)
        s = summary.setdefault(key, {"first": ts, "last": ts, "count": 0})
        s["last"] = ts
        s["count"] += 1

    if not summary:
        print(f"No drift detected. ({path} has no parseable entries.)")
        return 0

    print(f"{len(summary)} unique drift anomalies in {path}:\n")
    width_kind = max(len(k[1]) for k in summary)
    width_event = max(len(k[0]) for k in summary)
    width_field = max(len(k[2]) for k in summary)
    for event, kind, field in sorted(summary):
        s = summary[(event, kind, field)]
        print(
            f"  {kind:<{width_kind}}  {event:<{width_event}}  "
            f"{field:<{width_field}}  count={s['count']}  "
            f"first={s['first']}  last={s['last']}"
        )
    print(
        f"\nTo investigate: docs/verifying-hook-contract.md\nTo reset:       rm {path}"
    )
    return 0


def cmd_version(args) -> int:
    print(__version__)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claude-tap")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser(
        "install", help="Install ~/.claude-tap/bin/claude wrapper"
    ).set_defaults(fn=cmd_install)
    sub.add_parser("uninstall", help="Remove the wrapper").set_defaults(
        fn=cmd_uninstall
    )

    p_watch = sub.add_parser("watch", help="Subscribe to events.jsonl")
    p_watch.add_argument("--json", action="store_true", help="Print raw JSONL")
    p_watch.set_defaults(fn=cmd_watch)

    p_wm = sub.add_parser(
        "watch-messages",
        help="Subscribe to the derived ClaudeMessage stream",
    )
    p_wm.add_argument(
        "--from-start",
        action="store_true",
        help="Replay all sessions' transcripts from the beginning",
    )
    p_wm.add_argument(
        "--json",
        action="store_true",
        help="Print one JSON line per message instead of pretty blocks",
    )
    p_wm.set_defaults(fn=cmd_watch_messages)

    p_bridge = sub.add_parser(
        "bridge", help="Bind decision.sock and answer PermissionRequests"
    )
    p_bridge.add_argument(
        "--stdio", action="store_true", help="Manual decisions via stdin (default)"
    )
    p_bridge.add_argument(
        "--auto", choices=["allow", "deny"], help="Auto-decide (testing)"
    )
    p_bridge.set_defaults(fn=cmd_bridge)

    p_drift = sub.add_parser(
        "drift", help="Summarize schema-drift anomalies from drift.log"
    )
    p_drift.add_argument(
        "--raw", action="store_true", help="Print drift.log verbatim (no summary)"
    )
    p_drift.set_defaults(fn=cmd_drift)

    sub.add_parser("version", help="Print package version").set_defaults(fn=cmd_version)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "fn"):
        parser.print_help()
        return 2
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
