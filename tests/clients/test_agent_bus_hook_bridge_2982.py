"""Agent-bus hook bridge registers with inferred provider identity (#2982)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "scripts" / "agent-bus-hook-bridge.py"


def _load_bridge() -> object:
    spec = importlib.util.spec_from_file_location("agent_bus_hook_bridge", BRIDGE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_bus_hook_bridge"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_bridge_session_start_registers_without_fleet_agent_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("FLEET_AGENT", raising=False)
    monkeypatch.delenv("FLEET_SESSION", raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("FLEET_API_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("AGENT_BUS_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("RUNNER_DASHBOARD_REPO_ROOT", str(ROOT))

    bridge = _load_bridge()
    captured: dict[str, str] = {}

    class FakeClient:
        def __init__(self, *, agent: str, session: str) -> None:
            self.agent = agent
            self.session = session

    def fake_run_start(client, **kwargs):  # noqa: ANN001, ANN003
        captured["session"] = client.session
        return {"offline": False, "registered": True, "inbox_count": 0, "session": client.session}

    stdin_payload = json.dumps({"conversationId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"})

    with (
        mock.patch.object(sys, "stdin", mock.Mock(read=mock.Mock(return_value=stdin_payload))),
        mock.patch("fleet_client.FleetClient", FakeClient),
        mock.patch("agent_bus.session_hooks.session_start", fake_run_start),
    ):
        code = bridge.session_start()

    assert code == 0
    assert captured["session"].startswith("claude-")
