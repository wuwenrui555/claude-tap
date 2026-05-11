"""Incremental transcript reader + JSONL → ClaudeMessage parser.

Two responsibilities:

* :func:`read_incremental` — open a session transcript at a saved
  byte offset, read new lines, parse them into
  :class:`ClaudeMessage` objects, and return the new offset plus the
  parser's carry-over state.
* :func:`parse_entries` — pure parser from a list of JSONL dicts to
  a list of display-ready :class:`ClaudeMessage`. Ported from the
  legacy ``ccmux-backend`` parser with claude-tap-specific
  simplifications:

    * ``thinking`` blocks are dropped (claude-tap v0.2 non-goal).
    * ``is_complete`` is always True (no streaming partials).

The JSONL schema is reverse-engineered from Claude Code session
files (no official spec). Entry shape::

    {
      "type": "user" | "assistant" | "summary" | ...,
      "message": {
        "content": [
          {"type": "text", "text": ...},
          {"type": "tool_use", "id": "toolu_...", "name": "...", "input": {...}},
          {"type": "tool_result", "tool_use_id": "toolu_...", "content": ...},
          {"type": "thinking", "thinking": "..."}
        ]
      },
      "sessionId": "...", "timestamp": "...", "uuid": "..."
    }
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ClaudeMessage
from .tool_summary import (
    format_blockquote,
    format_edit_result,
    format_tool_result_text,
    format_tool_use_summary,
)

logger = logging.getLogger(__name__)

_NO_CONTENT_PLACEHOLDER = "(no content)"
_INTERRUPTED_TEXT = "[Request interrupted by user for tool use]"

_RE_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
_RE_COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>")
_RE_LOCAL_STDOUT = re.compile(
    r"<local-command-stdout>(.*?)</local-command-stdout>", re.DOTALL
)
_RE_SYSTEM_TAGS = re.compile(
    r"<(bash-input|bash-stdout|bash-stderr|local-command-caveat|system-reminder)"
)

# Tools whose raw input dict is propagated to ClaudeMessage.input.
# Kept narrow on purpose: non-prompt tool args can be large or sensitive
# (Edit / Bash payloads) so we whitelist instead of blacklist.
PROMPT_TOOL_INPUT_PASSTHROUGH = frozenset({"AskUserQuestion", "ExitPlanMode"})
# Backwards-compatible private alias retained for any callers that
# may have been picking up the old name.
_PROMPT_TOOL_INPUT_PASSTHROUGH = PROMPT_TOOL_INPUT_PASSTHROUGH


@dataclass
class PendingTool:
    """Internal state carried across :func:`read_incremental` calls.

    A ``tool_use`` block whose matching ``tool_result`` hasn't been
    seen yet is parked here so the result can pull the original
    summary out when it arrives in a later read.

    Treated as opaque by callers: pass the dict back in unchanged.
    """

    summary: str
    tool_name: str
    input_data: dict | None = None
    prompt_input: dict | None = None


@dataclass
class _ParsedSimple:
    """Compact classification used for local-command detection."""

    kind: str  # "local_command" | "local_command_invoke"
    text: str
    command_name: str | None = None


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def read_incremental(
    transcript_path: Path,
    session_id: str,
    last_offset: int,
    pending_tools: dict[str, PendingTool] | None = None,
    last_cmd_name: str | None = None,
    narrow: bool = False,
) -> tuple[list[ClaudeMessage], int, dict[str, PendingTool], str | None]:
    """Read new transcript bytes and parse them into ClaudeMessages.

    Parameters
    ----------
    transcript_path:
        Path to the session JSONL file. May not yet exist.
    session_id:
        Stamped onto every emitted :class:`ClaudeMessage`.
    last_offset:
        Byte position to seek to before reading. ``0`` means "from
        the start"; ``file size`` means "from now on".
    pending_tools, last_cmd_name:
        Carry-over state from the previous call. Pass ``None`` /
        ``None`` on the first call for a session.

    Returns
    -------
    (messages, new_offset, new_pending_tools, new_last_cmd_name)
        The new offset is the byte position after the last fully
        read line. New pending tools and last_cmd_name should be
        threaded into the next call.

    Failure modes
    -------------
    * File missing → returns no new messages, leaves offset
      unchanged. Caller retries on next event.
    * File shrank since last read (truncation / rotation) → offset
      reset to 0, ``pending_tools`` cleared, warning logged.
    * Individual lines that are not valid JSON → skipped silently.
    * OS error mid-read → no new messages, offset unchanged.
    """
    if pending_tools is None:
        pending_tools = {}
    try:
        size = transcript_path.stat().st_size
    except FileNotFoundError:
        return [], last_offset, pending_tools, last_cmd_name
    except OSError as e:
        logger.warning("transcript stat failed: %s", e)
        return [], last_offset, pending_tools, last_cmd_name

    if size < last_offset:
        logger.warning(
            "transcript %s shrank (size=%d < offset=%d), resetting",
            transcript_path,
            size,
            last_offset,
        )
        last_offset = 0
        pending_tools = {}
        last_cmd_name = None

    if size == last_offset:
        return [], last_offset, pending_tools, last_cmd_name

    try:
        with transcript_path.open(encoding="utf-8") as f:
            f.seek(last_offset)
            data = f.read()
            new_offset = f.tell()
    except OSError as e:
        logger.warning("transcript read failed: %s", e)
        return [], last_offset, pending_tools, last_cmd_name

    entries: list[dict] = []
    for raw_line in data.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    messages, new_pending, new_last_cmd = parse_entries(
        entries,
        session_id,
        pending_tools=pending_tools,
        last_cmd_name=last_cmd_name,
        narrow=narrow,
    )
    return messages, new_offset, new_pending, new_last_cmd


def parse_entries(
    entries: list[dict],
    session_id: str,
    *,
    pending_tools: dict[str, PendingTool] | None = None,
    last_cmd_name: str | None = None,
    narrow: bool = False,
) -> tuple[list[ClaudeMessage], dict[str, PendingTool], str | None]:
    """Parse JSONL entry dicts into display-ready ClaudeMessages.

    Stream-mode behavior: tool_use blocks whose tool_result hasn't
    been observed are kept in ``pending_tools`` and returned. Caller
    threads the dict back in on the next call so a tool_result that
    arrives in the next read still finds its tool_use.

    ``narrow`` mode (used by :class:`MessageStream`): suppress
    transcript-side emission of any content that the hook payload
    can produce directly — concretely, ``tool_use`` blocks and the
    associated ``ExitPlanMode`` plan-text emission. The
    ``pending_tools`` registration still happens so a later
    ``tool_result`` block can pair with the original tool's summary.
    """
    if pending_tools is None:
        pending_tools = {}
    else:
        pending_tools = dict(pending_tools)

    result: list[ClaudeMessage] = []

    for data in entries:
        msg_type = data.get("type")
        if msg_type not in ("user", "assistant"):
            continue

        entry_timestamp = data.get("timestamp")
        message = data.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content", "")
        if not isinstance(content, list):
            content = [{"type": "text", "text": str(content)}] if content else []

        # Local-command detection happens against the user-side text
        # representation only. local_command_invoke and local_command
        # entries arrive in separate JSONL lines; last_cmd_name carries
        # the slash command name across that boundary.
        parsed_simple = _parse_local_command(data)
        if parsed_simple is not None:
            if parsed_simple.kind == "local_command_invoke":
                last_cmd_name = parsed_simple.command_name
                continue
            if parsed_simple.kind == "local_command":
                cmd = parsed_simple.command_name or last_cmd_name or ""
                text = parsed_simple.text
                if cmd:
                    if "\n" in text:
                        formatted = f"❯ `{cmd}`\n```\n{text}\n```"
                    else:
                        formatted = f"❯ `{cmd}`\n`{text}`"
                else:
                    if "\n" in text:
                        formatted = f"```\n{text}\n```"
                    else:
                        formatted = f"`{text}`"
                result.append(
                    ClaudeMessage(
                        session_id=session_id,
                        role="assistant",
                        text=formatted,
                        content_type="local_command",
                        timestamp=entry_timestamp,
                    )
                )
                last_cmd_name = None
                continue
        # Any non-local-command user/assistant message clears the
        # pending slash-command name.
        last_cmd_name = None

        if msg_type == "assistant":
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")

                if btype == "text":
                    t = block.get("text", "").strip()
                    if t and t != _NO_CONTENT_PLACEHOLDER:
                        result.append(
                            ClaudeMessage(
                                session_id=session_id,
                                role="assistant",
                                text=t,
                                content_type="text",
                                timestamp=entry_timestamp,
                            )
                        )

                elif btype == "tool_use":
                    tool_id = block.get("id", "")
                    name = block.get("name", "unknown")
                    inp = block.get("input", {})
                    summary = format_tool_use_summary(name, inp)

                    # ExitPlanMode emits the plan content as an extra
                    # text message so it renders before the tool entry.
                    # In narrow mode the caller emits the plan from
                    # the hook payload, so suppress here.
                    if not narrow and name == "ExitPlanMode" and isinstance(inp, dict):
                        plan = inp.get("plan", "")
                        if plan:
                            result.append(
                                ClaudeMessage(
                                    session_id=session_id,
                                    role="assistant",
                                    text=plan,
                                    content_type="text",
                                    timestamp=entry_timestamp,
                                )
                            )

                    input_passthrough = (
                        inp
                        if isinstance(inp, dict)
                        and name in PROMPT_TOOL_INPUT_PASSTHROUGH
                        else None
                    )
                    # pending_tools registration runs in both modes:
                    # the matching tool_result still arrives via
                    # transcript and needs the original summary.
                    if tool_id:
                        input_data = (
                            inp
                            if name in ("Edit", "NotebookEdit")
                            and isinstance(inp, dict)
                            else None
                        )
                        pending_tools[tool_id] = PendingTool(
                            summary=summary,
                            tool_name=name,
                            input_data=input_data,
                            prompt_input=input_passthrough,
                        )
                    if not narrow:
                        result.append(
                            ClaudeMessage(
                                session_id=session_id,
                                role="assistant",
                                text=summary,
                                content_type="tool_use",
                                tool_use_id=tool_id or None,
                                tool_name=name,
                                input=input_passthrough,
                                timestamp=entry_timestamp,
                            )
                        )

                # `thinking` blocks are deliberately dropped per
                # claude-tap v0.2 policy (non-goal #2).

        elif msg_type == "user":
            user_text_parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    if isinstance(block, str) and block.strip():
                        user_text_parts.append(block.strip())
                    continue
                btype = block.get("type", "")

                if btype == "tool_result":
                    _emit_tool_result(
                        block,
                        session_id=session_id,
                        entry_timestamp=entry_timestamp,
                        pending_tools=pending_tools,
                        result=result,
                    )

                elif btype == "text":
                    t = block.get("text", "").strip()
                    if t and not _RE_SYSTEM_TAGS.search(t):
                        user_text_parts.append(t)

            if user_text_parts:
                combined = "\n".join(user_text_parts)
                if not _RE_LOCAL_STDOUT.search(
                    combined
                ) and not _RE_COMMAND_NAME.search(combined):
                    result.append(
                        ClaudeMessage(
                            session_id=session_id,
                            role="user",
                            text=combined,
                            content_type="text",
                            timestamp=entry_timestamp,
                        )
                    )

    for entry in result:
        entry.text = entry.text.strip()

    return result, pending_tools, last_cmd_name


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _emit_tool_result(
    block: dict,
    *,
    session_id: str,
    entry_timestamp: str | None,
    pending_tools: dict[str, PendingTool],
    result: list[ClaudeMessage],
) -> None:
    """Build a tool_result ClaudeMessage from a content block.

    Mutates ``pending_tools`` (pops the matching entry) and appends
    to ``result``. Handles interrupted, error, and normal cases.
    """
    tool_use_id: str | None = block.get("tool_use_id") or None
    result_content = block.get("content", "")
    result_text = _extract_tool_result_text(result_content)
    result_images = _extract_tool_result_images(result_content)
    is_error = bool(block.get("is_error", False))
    is_interrupted = result_text == _INTERRUPTED_TEXT

    tool_info = pending_tools.pop(tool_use_id, None) if tool_use_id else None
    if tool_info is None:
        tool_summary = None
        tool_name = None
        tool_input_data = None
    else:
        tool_summary = tool_info.summary
        tool_name = tool_info.tool_name
        tool_input_data = tool_info.input_data

    if is_interrupted:
        entry_text = tool_summary or ""
        entry_text = (entry_text + "\n⏹ Interrupted") if entry_text else "⏹ Interrupted"
        result.append(
            ClaudeMessage(
                session_id=session_id,
                role="assistant",
                text=entry_text,
                content_type="tool_result",
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                timestamp=entry_timestamp,
            )
        )
        return

    if is_error:
        entry_text = tool_summary or "**Error**"
        if result_text:
            error_summary = result_text.split("\n")[0]
            if len(error_summary) > 100:
                error_summary = error_summary[:100] + "…"
            entry_text += f"\n  ⎿  Error: {error_summary}"
            if "\n" in result_text:
                entry_text += "\n" + format_blockquote(result_text)
        else:
            entry_text += "\n  ⎿  Error"
        result.append(
            ClaudeMessage(
                session_id=session_id,
                role="assistant",
                text=entry_text,
                content_type="tool_result",
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                timestamp=entry_timestamp,
                image_data=result_images,
            )
        )
        return

    if tool_summary:
        entry_text = tool_summary
        if tool_name in ("Edit", "NotebookEdit") and tool_input_data and result_text:
            edit_block = format_edit_result(tool_input_data, result_text)
            if edit_block:
                entry_text += "\n" + edit_block
            elif result_text:
                entry_text += "\n" + format_tool_result_text(result_text, tool_name)
        elif result_text:
            entry_text += "\n" + format_tool_result_text(result_text, tool_name)
        result.append(
            ClaudeMessage(
                session_id=session_id,
                role="assistant",
                text=entry_text,
                content_type="tool_result",
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                timestamp=entry_timestamp,
                image_data=result_images,
            )
        )
        return

    if result_text or result_images:
        result.append(
            ClaudeMessage(
                session_id=session_id,
                role="assistant",
                text=format_tool_result_text(result_text, tool_name)
                if result_text
                else "",
                content_type="tool_result",
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                timestamp=entry_timestamp,
                image_data=result_images,
            )
        )


def _parse_local_command(data: dict) -> _ParsedSimple | None:
    """Detect local-command messages embedded as user-role text."""
    if data.get("type") != "user":
        return None
    message = data.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content", "")
    if isinstance(content, list):
        text = _extract_text_only(content)
    else:
        text = str(content) if content else ""
    text = _RE_ANSI_ESCAPE.sub("", text)
    if not text:
        return None

    stdout_match = _RE_LOCAL_STDOUT.search(text)
    if stdout_match:
        stdout = stdout_match.group(1).strip()
        cmd_match = _RE_COMMAND_NAME.search(text)
        cmd = cmd_match.group(1) if cmd_match else None
        return _ParsedSimple(kind="local_command", text=stdout, command_name=cmd)
    cmd_match = _RE_COMMAND_NAME.search(text)
    if cmd_match:
        return _ParsedSimple(
            kind="local_command_invoke", text="", command_name=cmd_match.group(1)
        )
    return None


def _extract_text_only(content_list: Any) -> str:
    """Concatenate the text blocks in a content list."""
    if not isinstance(content_list, list):
        if isinstance(content_list, str):
            return content_list
        return ""
    texts: list[str] = []
    for item in content_list:
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict) and item.get("type") == "text":
            t = item.get("text", "")
            if t:
                texts.append(t)
    return "\n".join(texts)


def _extract_tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                t = item.get("text", "")
                if t:
                    parts.append(t)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


def _extract_tool_result_images(content: Any) -> list[tuple[str, bytes]] | None:
    """Pull base64 images out of a tool_result content list, if any."""
    if not isinstance(content, list):
        return None
    images: list[tuple[str, bytes]] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "image":
            continue
        source = item.get("source")
        if not isinstance(source, dict) or source.get("type") != "base64":
            continue
        media_type = source.get("media_type", "image/png")
        data_str = source.get("data", "")
        if not data_str:
            continue
        try:
            raw_bytes = base64.b64decode(data_str)
            images.append((media_type, raw_bytes))
        except Exception:
            logger.debug("Failed to decode base64 image in tool_result")
    return images if images else None
