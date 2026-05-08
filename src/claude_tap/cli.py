"""claude-tap user-facing CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from . import __version__
from .config import wrapper_path
from .listener import DecisionListener
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
        summary = payload.get("prompt", "")[:60]
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


def _build_decision(verb: str | None) -> dict[str, Any]:
    if verb == "allow":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "permissionDecision": "allow",
            }
        }
    if verb == "deny":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "permissionDecision": "deny",
                "permissionDecisionReason": "denied via claude-tap bridge",
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
