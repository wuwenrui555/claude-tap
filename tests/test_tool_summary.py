"""Golden-output tests for tool_summary formatters."""

from __future__ import annotations

from claude_tap.tool_summary import (
    format_edit_diff,
    format_edit_result,
    format_tool_result_text,
    format_tool_use_summary,
)

# ---------- format_tool_use_summary ----------


def test_simple_field_tool():
    assert (
        format_tool_use_summary("Read", {"file_path": "foo.py"}) == "**Read**(foo.py)"
    )


def test_simple_field_tool_bash():
    assert format_tool_use_summary("Bash", {"command": "ls -la"}) == "**Bash**(ls -la)"


def test_glob_prefers_file_path():
    assert (
        format_tool_use_summary("Glob", {"file_path": "src/", "pattern": "*.py"})
        == "**Glob**(src/)"
    )


def test_glob_falls_back_to_pattern():
    assert format_tool_use_summary("Glob", {"pattern": "*.py"}) == "**Glob**(*.py)"


def test_edit_uses_file_path():
    assert (
        format_tool_use_summary(
            "Edit", {"file_path": "a.py", "old_string": "x", "new_string": "y"}
        )
        == "**Edit**(a.py)"
    )


def test_todowrite_count():
    assert (
        format_tool_use_summary("TodoWrite", {"todos": [1, 2, 3]})
        == "**TodoWrite**(3 item(s))"
    )


def test_ask_user_question_first_question():
    inp = {"questions": [{"question": "Pick one"}, {"question": "Other"}]}
    assert (
        format_tool_use_summary("AskUserQuestion", inp)
        == "**AskUserQuestion**(Pick one)"
    )


def test_bare_summary_tools():
    assert format_tool_use_summary("ExitPlanMode", {"plan": "x"}) == "**ExitPlanMode**"
    assert format_tool_use_summary("TodoRead", {}) == "**TodoRead**"


def test_unknown_tool_first_string_value():
    assert (
        format_tool_use_summary("Mystery", {"foo": "", "bar": "hello", "baz": "world"})
        == "**Mystery**(hello)"
    )


def test_unknown_tool_no_string_values():
    assert format_tool_use_summary("Mystery", {"n": 5, "items": []}) == "**Mystery**"


def test_truncates_long_summary():
    long_path = "a" * 250
    out = format_tool_use_summary("Read", {"file_path": long_path})
    # 200 + ellipsis
    assert out == f"**Read**({'a' * 200}…)"


def test_non_dict_input():
    assert format_tool_use_summary("Read", None) == "**Read**"
    assert format_tool_use_summary("Read", "string") == "**Read**"


# ---------- format_tool_result_text ----------


def test_read_result_shows_line_count():
    body = "line1\nline2\nline3"
    assert format_tool_result_text(body, "Read") == "  ⎿  Read 3 lines"


def test_write_result_shows_line_count():
    assert format_tool_result_text("a\nb", "Write") == "  ⎿  Wrote 2 lines"


def test_bash_result_includes_blockquote():
    out = format_tool_result_text("hello\nworld", "Bash")
    assert "  ⎿  Output 2 lines" in out
    assert "> hello" in out
    assert "> world" in out


def test_grep_result_match_count():
    body = "match1\nmatch2\nmatch3"
    out = format_tool_result_text(body, "Grep")
    assert "  ⎿  Found 3 matches" in out


def test_empty_result_returns_empty():
    assert format_tool_result_text("", "Read") == ""


def test_default_blockquote_for_unknown_tool():
    out = format_tool_result_text("foo", "UnknownTool")
    assert out == "> foo"


# ---------- format_edit_diff ----------


def test_edit_diff_basic():
    diff = format_edit_diff("hello\n", "world\n")
    assert "-hello" in diff
    assert "+world" in diff
    # Header lines stripped
    assert "---" not in diff
    assert "+++" not in diff


def test_edit_diff_unchanged_returns_empty():
    diff = format_edit_diff("hello", "hello")
    assert diff == ""


# ---------- format_edit_result ----------


def test_edit_result_with_diff():
    out = format_edit_result({"old_string": "foo\n", "new_string": "bar\n"}, "ignored")
    assert "Added 1 lines, removed 1 lines" in out
    assert "> -foo" in out
    assert "> +bar" in out


def test_edit_result_missing_inputs():
    assert format_edit_result({}, "x") == ""
    assert format_edit_result({"old_string": "a"}, "x") == ""
    assert format_edit_result(None, "x") == ""  # type: ignore[arg-type]
