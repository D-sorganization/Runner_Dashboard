"""Agent bus wiring in fleet hook adapters (#2155 slice 2, #2982)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from shared_scripts.agent_hook_adapters import (
    AGY_BARB_HOOK_GROUP,
    AGY_BARB_HOOK_REL,
    agent_bus_bridge_command,
    barb_hook_command,
    check_adapters,
    generate_antigravity_hooks,
    generate_codex_hooks,
    generate_cursor_hooks,
    generate_pre_push_sample,
    merge_claude_settings,
    validate_antigravity_hooks,
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
def test_agy_stop_hook_runs_barb_wrapper_from_generated_hooks() -> None:
    agy = generate_antigravity_hooks(Path("/tmp"), timeout_seconds=90)
    stops = agy[AGY_BARB_HOOK_GROUP]["Stop"]
    assert stops == [{"type": "command", "command": barb_hook_command(AGY_BARB_HOOK_REL), "timeout": 90}]
    assert "scripts/wrappers/agy_barb_hook.py" in stops[0]["command"]
    validate_antigravity_hooks(agy)


@pytest.mark.unit
def test_agy_barb_stop_hook_is_idempotent_and_preserves_user_hooks(tmp_path: Path) -> None:
    (tmp_path / ".agents").mkdir()
    custom = {"type": "command", "command": "echo mine"}
    (tmp_path / ".agents/hooks.json").write_text(json.dumps({AGY_BARB_HOOK_GROUP: {"Stop": [custom]}}))
    first = generate_antigravity_hooks(tmp_path, timeout_seconds=90)
    (tmp_path / ".agents/hooks.json").write_text(json.dumps(first))
    second = generate_antigravity_hooks(tmp_path, timeout_seconds=90)
    assert second == first
    commands = [h["command"] for h in second[AGY_BARB_HOOK_GROUP]["Stop"]]
    assert commands == ["echo mine", barb_hook_command(AGY_BARB_HOOK_REL)]


@pytest.mark.unit
def test_committed_agents_hooks_json_has_no_drift() -> None:
    root = Path(__file__).resolve().parents[2]
    assert check_adapters(root, targets=frozenset({"antigravity"})) == []


@pytest.mark.unit
def test_pre_push_sample_runs_agent_bus_before_agent_check() -> None:
    text = generate_pre_push_sample()
    assert "agent-bus-hook-bridge.py" in text
    assert "pre_push_agent_check.py" in text
