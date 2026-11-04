"""Agent-bus hook bridge survives a pre-3.11 system python3 (#3980)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "scripts" / "agent-bus-hook-bridge.py"


def _load_bridge() -> object:
    spec = importlib.util.spec_from_file_location("agent_bus_hook_bridge_3980", BRIDGE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_supported_python_proceeds() -> None:
    bridge = _load_bridge()
    assert bridge.ensure_supported_python(["session-start"], (3, 11, 0)) is None


@pytest.mark.unit
def test_old_python_reexecs_under_newest_python3x(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_BUS_HOOK_REEXEC", raising=False)
    bridge = _load_bridge()
    available = {"python3.11": "/usr/bin/python3.11", "python3.12": "/usr/bin/python3.12"}
    with (
        mock.patch.object(bridge.shutil, "which", side_effect=available.get),
        mock.patch.object(bridge.os, "execv") as execv,
    ):
        bridge.ensure_supported_python(["session-start"], (3, 10, 12))

    execv.assert_called_once_with(
        "/usr/bin/python3.12", ["/usr/bin/python3.12", str(BRIDGE.resolve()), "session-start"]
    )
    assert os.environ["AGENT_BUS_HOOK_REEXEC"] == "1"


@pytest.mark.unit
def test_old_python_without_candidate_fails_open(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("AGENT_BUS_HOOK_REEXEC", raising=False)
    bridge = _load_bridge()
    with (
        mock.patch.object(bridge.shutil, "which", return_value=None),
        mock.patch.object(bridge.os, "execv") as execv,
    ):
        code = bridge.ensure_supported_python(["session-start"], (3, 10, 12))

    assert code == 0
    execv.assert_not_called()
    err = capsys.readouterr().err
    assert "3.10" in err
    assert "Traceback" not in err


@pytest.mark.unit
def test_reexec_guard_prevents_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_HOOK_REEXEC", "1")
    bridge = _load_bridge()
    with (
        mock.patch.object(bridge.shutil, "which", return_value="/usr/bin/python3.12"),
        mock.patch.object(bridge.os, "execv") as execv,
    ):
        code = bridge.ensure_supported_python(["session-start"], (3, 10, 12))

    assert code == 0
    execv.assert_not_called()


@pytest.mark.unit
def test_main_stops_before_backend_import_on_old_python(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _load_bridge()
    monkeypatch.setattr(bridge, "ensure_supported_python", lambda args: 0)
    with mock.patch.object(bridge, "session_start") as session_start:
        assert bridge.main(["session-start"]) == 0
    session_start.assert_not_called()


@pytest.mark.unit
def test_missing_backend_dependency_fails_open(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge = _load_bridge()
    monkeypatch.setattr(bridge, "ensure_supported_python", lambda args: None)

    def broken_start() -> int:
        raise ModuleNotFoundError("No module named 'fastapi'")

    monkeypatch.setattr(bridge, "session_start", broken_start)
    assert bridge.main(["session-start"]) == 0
    assert "fastapi" in capsys.readouterr().err
