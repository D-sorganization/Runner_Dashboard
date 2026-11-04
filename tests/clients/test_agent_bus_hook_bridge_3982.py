"""Regression tests for agent-bus hook bridge backend-import fail-open (#3982)."""

from __future__ import annotations

import importlib.util
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
def test_session_start_fails_open_when_backend_import_raises(
    capsys: pytest.CaptureFixture[str],
) -> None:
    bridge = _load_bridge()
    with (
        mock.patch.object(bridge, "ensure_python_runtime", return_value=True),
        mock.patch.object(bridge, "_paths", return_value=Path("/tmp/dashboard-root")),
        mock.patch.dict(
            sys.modules,
            {"agent_bus.session_hooks": None, "fleet_client": None, "fleet_provider_identity": None},
        ),
    ):
        exit_code = bridge.session_start()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.err.count("\n") == 1
    assert "agent-bus-hook-bridge" in captured.err
    assert "ModuleNotFoundError" in captured.err or "ImportError" in captured.err
    assert captured.out == ""


@pytest.mark.unit
def test_pre_push_fails_open_when_backend_import_raises(
    capsys: pytest.CaptureFixture[str],
) -> None:
    bridge = _load_bridge()
    with (
        mock.patch.object(bridge, "ensure_python_runtime", return_value=True),
        mock.patch.object(bridge, "_paths", return_value=Path("/tmp/dashboard-root")),
        mock.patch.dict(sys.modules, {"agent_bus.session_hooks": None}),
    ):
        exit_code = bridge.pre_push()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.err.count("\n") == 1
    assert "agent-bus-hook-bridge" in captured.err
    assert captured.out == ""


@pytest.mark.unit
def test_main_session_start_fails_open_under_backend_import_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    bridge = _load_bridge()
    with (
        mock.patch.object(bridge, "ensure_python_runtime", return_value=True),
        mock.patch.object(bridge, "_paths", return_value=Path("/tmp/dashboard-root")),
        mock.patch.dict(
            sys.modules,
            {"agent_bus.session_hooks": None, "fleet_client": None, "fleet_provider_identity": None},
        ),
    ):
        exit_code = bridge.main(["session-start"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.out == ""
