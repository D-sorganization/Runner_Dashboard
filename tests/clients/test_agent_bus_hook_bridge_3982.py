"""Regression tests: hook bridge fails open when backend deps are missing (#3982)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "scripts" / "agent-bus-hook-bridge.py"


def _load_bridge() -> object:
    spec = importlib.util.spec_from_file_location("agent_bus_hook_bridge_3982", BRIDGE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["session-start", "pre-push"])
def test_bridge_fails_open_when_backend_imports_missing(
    mode: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bridge = _load_bridge()
    # A None entry makes `import agent_bus.session_hooks` raise ModuleNotFoundError,
    # matching a re-exec'd interpreter without fastapi installed.
    with (
        mock.patch.object(bridge, "_paths", return_value=ROOT),
        mock.patch.dict(sys.modules, {"agent_bus.session_hooks": None}),
    ):
        exit_code = bridge.main([mode])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "agent-bus-hook-bridge" in captured.err
    assert "backend dependencies unavailable" in captured.err
    assert captured.err.count("\n") == 1
    assert captured.out == ""
