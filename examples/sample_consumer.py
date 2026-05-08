#!/usr/bin/env python3
"""Reference consumer for claude-tap.

Subscribes to events (via EventStream — looks like push, no manual
tail) and binds decision.sock to answer PermissionRequests
interactively from stdin.

Copy this file as a starting point for your own consumer (chat
backend, GUI, ...).
"""

import asyncio
import json
import sys

from claude_tap import DecisionListener, EventStream


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

        if et == "user_prompt_submit":
            summary = payload.get("prompt", "")[:60]
        elif et in ("pre_tool_use", "post_tool_use"):
            summary = (
                f"{payload.get('tool_name')}"
                f"({json.dumps(payload.get('tool_input', {}), ensure_ascii=False)[:50]})"
            )
        elif et == "permission_request":
            summary = f"⚠️  {payload.get('tool_name')} needs decision"
        else:
            summary = ""

        print(f"[{et:<22}] sess={sid} win={win:>4} {summary}")


async def serve_decisions() -> None:
    """For each PermissionRequest, prompt operator via stdin.

    A real consumer would route to a chat platform / a GUI / an LLM
    judge instead of blocking on stdin.
    """
    async with DecisionListener() as listener:
        async for req in listener:
            print("\n>>> Decision needed")
            print(f"    session   = {req.session_id}")
            print(f"    tool      = {req.tool_name}")
            print(
                f"    input     = "
                f"{json.dumps(req.tool_input, ensure_ascii=False, indent=2)}"
            )
            for s in req.permission_suggestions or []:
                print(f"    suggestion= {s}")
            print(
                "    type 'allow' or 'deny' (default deny): ",
                end="",
                flush=True,
            )

            answer = (await asyncio.to_thread(sys.stdin.readline)).strip().lower()
            allow = answer == "allow"

            decision = {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "permissionDecision": "allow" if allow else "deny",
                    **(
                        {"permissionDecisionReason": "denied via sample consumer"}
                        if not allow
                        else {}
                    ),
                }
            }
            await listener.respond(req.request_id, decision)
            print(f"<<< sent: {decision['hookSpecificOutput']['permissionDecision']}\n")


async def main() -> None:
    await asyncio.gather(watch_events(), serve_decisions())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
