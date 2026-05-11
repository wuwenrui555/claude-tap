"""Pure formatters for tool_use and tool_result display strings.

Used by :mod:`claude_tap.transcript` while parsing JSONL entries into
:class:`ClaudeMessage`. No I/O, no global state, no dependencies on
the rest of claude-tap.

The constants `_SIMPLE_SUMMARY_FIELDS` and `_BARE_SUMMARY_TOOLS` are
inlined here rather than imported from `claude_code_state` to keep
claude-tap's dependency surface small (claude_code_state pulls in
tmux-related machinery that is unrelated to message reconstruction).
"""

from __future__ import annotations

import difflib
from typing import Any

# One-field tools: tool name -> input dict key to surface as summary.
_SIMPLE_SUMMARY_FIELDS: dict[str, str] = {
    "Read": "file_path",
    "Write": "file_path",
    "Bash": "command",
    "Grep": "pattern",
    "Task": "description",
    "WebFetch": "url",
    "WebSearch": "query",
    "Skill": "skill",
}

# Tools that intentionally render as bare "**Name**" with no argument.
_BARE_SUMMARY_TOOLS: frozenset[str] = frozenset({"TodoRead", "ExitPlanMode"})

_MAX_SUMMARY_LENGTH = 200


def format_tool_use_summary(name: str, input_data: Any) -> str:
    """Format a tool_use block into a brief summary line.

    Returns a string like ``"**Read**(file.py)"``. Long arguments are
    truncated at :data:`_MAX_SUMMARY_LENGTH`. Unknown tools fall back
    to the first non-empty string value in their input dict, or to
    a bare ``"**Name**"`` if no such value exists.
    """
    if not isinstance(input_data, dict):
        return f"**{name}**"

    summary = ""
    if name in _SIMPLE_SUMMARY_FIELDS:
        summary = input_data.get(_SIMPLE_SUMMARY_FIELDS[name], "")
    elif name == "Glob":
        summary = input_data.get("file_path") or input_data.get("pattern", "")
    elif name in ("Edit", "NotebookEdit"):
        # Diff and stats are generated in the tool_result stage; here we
        # only surface the target path.
        summary = input_data.get("file_path") or input_data.get("notebook_path", "")
    elif name == "TodoWrite":
        todos = input_data.get("todos", [])
        if isinstance(todos, list):
            summary = f"{len(todos)} item(s)"
    elif name == "AskUserQuestion":
        questions = input_data.get("questions", [])
        if isinstance(questions, list) and questions:
            q = questions[0]
            if isinstance(q, dict):
                summary = q.get("question", "")
    elif name in _BARE_SUMMARY_TOOLS:
        summary = ""
    else:
        for v in input_data.values():
            if isinstance(v, str) and v:
                summary = v
                break

    if summary:
        if len(summary) > _MAX_SUMMARY_LENGTH:
            summary = summary[:_MAX_SUMMARY_LENGTH] + "…"
        return f"**{name}**({summary})"
    return f"**{name}**"


def format_blockquote(text: str) -> str:
    """Prefix every line with ``> `` so the output is valid CommonMark."""
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))


def format_edit_diff(old_string: str, new_string: str) -> str:
    """Generate a compact unified diff between old_string and new_string.

    Strips the ``---`` / ``+++`` header lines so the diff renders
    cleanly when wrapped in a markdown blockquote.
    """
    old_lines = old_string.splitlines(keepends=True)
    new_lines = new_string.splitlines(keepends=True)
    diff = difflib.unified_diff(old_lines, new_lines, lineterm="")
    result_lines: list[str] = []
    for line in diff:
        if line.startswith("---") or line.startswith("+++"):
            continue
        result_lines.append(line.rstrip("\n"))
    return "\n".join(result_lines)


def format_tool_result_text(text: str, tool_name: str | None = None) -> str:
    """Format tool_result body with per-tool stats line.

    No truncation here; truncation belongs to the consumer's send
    layer. Always emits a blockquote for the body so a chat-style
    consumer can collapse it.
    """
    if not text:
        return ""

    line_count = text.count("\n") + 1 if text else 0

    if tool_name == "Read":
        return f"  ⎿  Read {line_count} lines"

    if tool_name == "Write":
        return f"  ⎿  Wrote {line_count} lines"

    if tool_name == "Bash":
        if line_count > 0:
            stats = f"  ⎿  Output {line_count} lines"
            return stats + "\n" + format_blockquote(text)
        return format_blockquote(text)

    if tool_name == "Grep":
        matches = len([line for line in text.split("\n") if line.strip()])
        stats = f"  ⎿  Found {matches} matches"
        return stats + "\n" + format_blockquote(text)

    if tool_name == "Glob":
        files = len([line for line in text.split("\n") if line.strip()])
        stats = f"  ⎿  Found {files} files"
        return stats + "\n" + format_blockquote(text)

    if tool_name == "Task":
        if line_count > 0:
            stats = f"  ⎿  Agent output {line_count} lines"
            return stats + "\n" + format_blockquote(text)
        return format_blockquote(text)

    if tool_name == "WebFetch":
        char_count = len(text)
        stats = f"  ⎿  Fetched {char_count} characters"
        return stats + "\n" + format_blockquote(text)

    if tool_name == "WebSearch":
        results = text.count("\n\n") + 1 if text else 0
        stats = f"  ⎿  {results} search results"
        return stats + "\n" + format_blockquote(text)

    return format_blockquote(text)


def format_edit_result(input_data: dict, result_text: str) -> str:
    """Format an Edit/NotebookEdit tool_result with diff stats + blockquote.

    Returns ``""`` if the input is missing the required fields.
    """
    if not isinstance(input_data, dict):
        return ""
    old_s = input_data.get("old_string", "")
    new_s = input_data.get("new_string", "")
    if not old_s or not new_s:
        return ""
    diff_text = format_edit_diff(old_s, new_s)
    if not diff_text:
        return ""
    added = sum(
        1
        for line in diff_text.split("\n")
        if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1
        for line in diff_text.split("\n")
        if line.startswith("-") and not line.startswith("---")
    )
    stats = f"  ⎿  Added {added} lines, removed {removed} lines"
    return stats + "\n" + format_blockquote(diff_text)
