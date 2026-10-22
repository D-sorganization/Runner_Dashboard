"""Agent bus wiring in fleet hook adapters (#2155 slice 2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from shared_scripts.agent_hook_adapters import generate_cursor_hooks, generate_pre_push_sample


@pytest.mark.unit
def test_cursor_hooks_include_session_start_agent_bus() -> None:
    doc = generate_cursor_hooks(Path("/tmp"), timeout_seconds=60)
    starts = doc["hooks"]["sessionStart"]
    assert starts[0]["command"] == "python3 scripts/agent-bus-hook-bridge.py session-start"


@pytest.mark.unit
def test_pre_push_sample_runs_agent_bus_before_agent_check() -> None:
    text = generate_pre_push_sample()
    assert "agent-bus-hook-bridge.py" in text
    assert "pre_push_agent_check.py" in text
