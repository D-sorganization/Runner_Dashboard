"""Regression tests for agent-bus hook bridge Python 3.10 compatibility (#3980)."""

from __future__ import annotations

import importlib.util
import os
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
def test_ensure_python_runtime_no_op_on_modern_python() -> None:
    bridge = _load_bridge()
    with mock.patch.object(sys, "version_info", (3, 11, 2, "final", 0)):
        assert bridge.ensure_python_runtime() is True


@pytest.mark.unit
def test_ensure_python_runtime_reexecs_when_candidate_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _load_bridge()
    monkeypatch.delenv(bridge.REEXEC_ENV_VAR, raising=False)

    def fake_which(cmd: str) -> str | None:
        if cmd == "python3.12":
            return "/usr/bin/python3.12"
        return None

    exec_called: dict[str, object] = {}

    def fake_execvpe(file: str, args: list[str], env: dict[str, str]) -> None:
        exec_called["file"] = file
        exec_called["args"] = args
        exec_called["env_flag"] = env.get(bridge.REEXEC_ENV_VAR)

    with (
        mock.patch.object(sys, "version_info", (3, 10, 12, "final", 0)),
        mock.patch("shutil.which", side_effect=fake_which),
        mock.patch("os.execvpe", side_effect=fake_execvpe),
    ):
        bridge.ensure_python_runtime()

    assert exec_called["file"] == "/usr/bin/python3.12"
    assert exec_called["env_flag"] == "1"


@pytest.mark.unit
def test_ensure_python_runtime_fails_open_when_no_candidate(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _load_bridge()
    monkeypatch.delenv(bridge.REEXEC_ENV_VAR, raising=False)

    with (
        mock.patch.object(sys, "version_info", (3, 10, 12, "final", 0)),
        mock.patch("shutil.which", return_value=None),
    ):
        res = bridge.ensure_python_runtime()

    assert res is False
    captured = capsys.readouterr()
    assert "agent-bus-hook-bridge" in captured.err
    assert "python >= 3.11 required" in captured.err
    assert "3.10.12" in captured.err
    assert captured.err.count("\n") == 1  # exactly one stderr line


@pytest.mark.unit
def test_ensure_python_runtime_prevents_loop_when_flag_set(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _load_bridge()
    monkeypatch.setenv(bridge.REEXEC_ENV_VAR, "1")

    with (
        mock.patch.object(sys, "version_info", (3, 10, 12, "final", 0)),
        mock.patch("os.execvpe") as mock_exec,
    ):
        res = bridge.ensure_python_runtime()

    mock_exec.assert_not_called()
    assert res is False
    captured = capsys.readouterr()
    assert "agent-bus-hook-bridge" in captured.err
    assert captured.err.count("\n") == 1


@pytest.mark.unit
def test_main_session_start_fails_open_under_python_310_no_traceback(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _load_bridge()
    monkeypatch.delenv(bridge.REEXEC_ENV_VAR, raising=False)

    with (
        mock.patch.object(sys, "version_info", (3, 10, 12, "final", 0)),
        mock.patch("shutil.which", return_value=None),
    ):
        exit_code = bridge.main(["session-start"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "ImportError" not in captured.err
    assert "python >= 3.11 required" in captured.err
    assert captured.out == ""
