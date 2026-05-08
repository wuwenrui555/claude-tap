import shutil
import subprocess

import pytest

from claude_tap.wrapper import build_settings_json, render_wrapper


def test_settings_has_all_eight_hooks():
    s = build_settings_json()
    hooks = s["hooks"]
    assert set(hooks.keys()) == {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Notification",
        "Stop",
        "SessionEnd",
        "PermissionRequest",
    }


def test_each_hook_has_command_and_timeout():
    s = build_settings_json()
    for event_name, group in s["hooks"].items():
        assert isinstance(group, list) and len(group) == 1
        entry = group[0]
        assert entry["matcher"] == ""
        inner = entry["hooks"][0]
        assert inner["type"] == "command"
        assert inner["command"] == f"claude-tap-hook {event_name}"
        assert isinstance(inner["timeout"], int)


def test_permission_request_timeout_is_125():
    s = build_settings_json()
    assert s["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"] == 125


def test_session_end_timeout_short():
    s = build_settings_json()
    assert s["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"] == 2


def test_render_wrapper_starts_with_shebang():
    body = render_wrapper()
    assert body.startswith("#!/usr/bin/env bash\n")


def test_render_wrapper_contains_settings_json():
    body = render_wrapper()
    assert "claude-tap-hook PermissionRequest" in body
    assert "__HOOKS_JSON__" not in body


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_render_wrapper_is_syntactically_valid_bash(tmp_path):
    """`bash -n` parse-checks without executing."""
    body = render_wrapper()
    f = tmp_path / "claude"
    f.write_text(body)
    result = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
    assert result.returncode == 0, f"bash -n failed: {result.stderr}"
