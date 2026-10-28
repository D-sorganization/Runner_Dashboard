"""Agent bus wiring in fleet hook adapters (#2155 slice 2, #2982)."""

from __future__ import annotations

from pathlib import Path

import pytest
from shared_scripts.agent_hook_adapters import (
    agent_bus_bridge_command,
    generate_antigravity_hooks,
    generate_codex_hooks,
    generate_cursor_hooks,
    generate_pre_push_sample,
    merge_claude_settings,
)


@pytest.mark.unit
def test_cursor_hooks_include_session_start_agent_bus() -> None:
    doc = generate_cursor_hooks(Path("/tmp"), timeout_seconds=60)
    starts = doc["hooks"]["sessionStart"]
    assert starts[0]["command"] == agent_bus_bridge_command("session-start")


@pytest.mark.unit
def test_claude_codex_and_agy_install_session_start_bus() -> None:
    timeout = 90
    claude = merge_claude_settings({"outputStyle": "Proactive"}, timeout_seconds=timeout)
    claude_cmd = claude["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert claude_cmd == agent_bus_bridge_command("session-start")

    codex = generate_codex_hooks(Path("/tmp"), timeout_seconds=timeout)
    codex_cmd = codex["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert codex_cmd == agent_bus_bridge_command("session-start")

    agy = generate_antigravity_hooks(Path("/tmp"), timeout_seconds=timeout)
    agy_cmd = agy["fleet-agent-bus"]["SessionStart"][0]["command"]
    assert agy_cmd == agent_bus_bridge_command("session-start")


@pytest.mark.unit
def test_pre_push_sample_runs_agent_bus_before_agent_check() -> None:
    text = generate_pre_push_sample()
    assert "agent-bus-hook-bridge.py" in text
    assert "pre_push_agent_check.py" in text
