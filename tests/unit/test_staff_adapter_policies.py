"""Unit tests for staff adapter_policies (#1484, #1586, #1595)."""

from __future__ import annotations

import pytest
from staff.adapter_policies import (
    CLAUDE_WRITE_TOOLS,
    PERMISSION_BYPASS_FLAGS,
    claude_allowed_tools,
    claude_unattended_tools,
    gemini_policy_toml,
)


@pytest.mark.unit
def test_claude_unattended_tools_formatting() -> None:
    allowed, denied = claude_unattended_tools()
    assert "Bash(git:*)" in allowed
    assert "Bash(python:*)" in allowed
    assert "Read" in allowed
    assert "Edit" in allowed
    assert "Bash(sudo:*)" in denied
    assert "Bash(gh repo delete:*)" in denied


@pytest.mark.unit
def test_gemini_policy_toml_content() -> None:
    toml = gemini_policy_toml()
    assert 'toolName = "run_shell_command"' in toml
    assert '"git"' in toml
    assert '"sudo"' in toml
    assert 'decision = "allow"' in toml
    assert 'decision = "deny"' in toml


@pytest.mark.unit
def test_claude_allowed_tools_mapping() -> None:
    tools = claude_allowed_tools(["view_file", "search_code"])
    assert "Read" in tools
    assert "Grep" in tools
    assert "Glob" in tools
    # Ensure no write tools leaked
    for write_tool in CLAUDE_WRITE_TOOLS:
        assert write_tool not in tools


@pytest.mark.unit
def test_claude_allowed_tools_unknown_raises() -> None:
    with pytest.raises(ValueError, match="not read-only chat tools"):
        claude_allowed_tools(["not_a_valid_tool"])


@pytest.mark.unit
def test_permission_bypass_flags_contents() -> None:
    assert "--dangerously-skip-permissions" in PERMISSION_BYPASS_FLAGS
    assert "--force" in PERMISSION_BYPASS_FLAGS
    assert "yolo" in PERMISSION_BYPASS_FLAGS
