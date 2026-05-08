"""Hook entry point invoked by Claude per registered hook event.

Usage: claude-tap-hook <event_name>

Reads stdin (Claude's hook payload, JSON), writes a normalized event to
events.jsonl, and (for PermissionRequest only) blocks on a unix socket
round-trip with whoever owns decision.sock. Prints the decision JSON
(or {}) to stdout for Claude to read.
"""

from __future__ import annotations

import json
import sys
import uuid
from typing import Any

from . import drift
from .config import (
    decision_sock_path,
    decision_timeout,
    events_path,
    surface_id,
)
from .events import ClaudeInfo, Event, append_jsonl, now_isoformat
from .socket_proto import try_socket_decision
from .tmux import read_tmux_info_from_env

_EVENT_NAME_MAP = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "user_prompt_submit",
    "PreToolUse": "pre_tool_use",
    "PostToolUse": "post_tool_use",
    "Notification": "notification",
    "Stop": "stop",
    "SessionEnd": "session_end",
    "PermissionRequest": "permission_request",
}


def normalize_event_name(claude_event_name: str) -> str:
    return _EVENT_NAME_MAP.get(claude_event_name, claude_event_name.lower())


def claude_info_from_payload(raw: dict[str, Any]) -> ClaudeInfo:
    return ClaudeInfo(
        session_id=raw.get("session_id", ""),
        transcript_path=raw.get("transcript_path", ""),
        cwd=raw.get("cwd", ""),
        permission_mode=raw.get("permission_mode", ""),
    )


def extract_payload(event_name: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Pull event-specific payload out of Claude's hook stdin JSON."""
    if event_name == "UserPromptSubmit":
        return {"prompt": raw.get("prompt", "")}
    if event_name == "PreToolUse":
        return {
            "tool_name": raw.get("tool_name", ""),
            "tool_input": raw.get("tool_input", {}),
        }
    if event_name == "PostToolUse":
        return {
            "tool_name": raw.get("tool_name", ""),
            "tool_input": raw.get("tool_input", {}),
            "tool_response": raw.get("tool_response", {}),
        }
    if event_name == "Notification":
        return {"message": raw.get("message", "")}
    if event_name == "SessionEnd":
        return {"reason": raw.get("reason", "")}
    if event_name == "PermissionRequest":
        return {
            "request_id": "",  # filled in by run()
            "tool_name": raw.get("tool_name", ""),
            "tool_input": raw.get("tool_input", {}),
            "permission_suggestions": raw.get("permission_suggestions", []),
        }
    return {}


def build_event(
    event_name: str,
    raw: dict[str, Any],
    request_id: str = "",
) -> Event:
    payload = extract_payload(event_name, raw)
    if event_name == "PermissionRequest" and request_id:
        payload["request_id"] = request_id
    return Event(
        event_type=normalize_event_name(event_name),
        timestamp=now_isoformat(),
        claude=claude_info_from_payload(raw),
        tmux=read_tmux_info_from_env(),
        surface_id=surface_id(),
        payload=payload,
    )


def _generate_request_id() -> str:
    return "r-" + uuid.uuid4().hex[:12]


def run(event_name: str, stdin_text: str) -> str:
    """Process one hook invocation. Returns stdout text."""
    try:
        raw = json.loads(stdin_text) if stdin_text.strip() else {}
    except json.JSONDecodeError:
        raw = {}

    # Best-effort schema-drift check. Logs to drift.log; never raises.
    drift.check(event_name, raw)

    request_id = _generate_request_id() if event_name == "PermissionRequest" else ""
    event = build_event(event_name, raw, request_id=request_id)

    try:
        append_jsonl(events_path(), event)
    except OSError as e:
        print(f"claude-tap: failed to append events.jsonl: {e}", file=sys.stderr)

    if event_name != "PermissionRequest":
        return "{}"

    # The wire format adds session_id alongside the payload fields so the
    # listener can route by session without re-parsing the event envelope.
    request = {**event.payload, "session_id": event.claude.session_id}
    decision = try_socket_decision(
        sock_path=decision_sock_path(),
        request=request,
        timeout=decision_timeout(),
    )
    if not decision:
        return "{}"
    return json.dumps(decision, ensure_ascii=False)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: claude-tap-hook <event_name>", file=sys.stderr)
        return 2
    event_name = sys.argv[1]
    stdin_text = sys.stdin.read()
    sys.stdout.write(run(event_name, stdin_text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
