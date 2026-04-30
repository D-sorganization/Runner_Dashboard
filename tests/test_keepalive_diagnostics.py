"""Keepalive diagnostics tests.

These tests do not perform real network or subprocess calls — `run_cmd` and
`os` are monkeypatched in every test. They are therefore safe to run in PR
CI and are NOT marked `@pytest.mark.integration`.

TODO(#434): if future tests in this module are added that exercise real
systemd/PowerShell calls, mark them `@pytest.mark.integration` so they are
excluded from PR CI by default and run nightly only (see issue #401).
"""

from __future__ import annotations  # noqa: E402

import asyncio  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import server  # noqa: E402  # noqa: E402


def _patch_server_windows_os(monkeypatch) -> None:
    real_os = server.os

    class WindowsOs(SimpleNamespace):
        name = "nt"

        def __getattr__(self, key: str):  # noqa: ANN202
            return getattr(real_os, key)

    monkeypatch.setattr(server, "os", WindowsOs())


def test_windows_wslconfig_path_is_checked_directly(monkeypatch, tmp_path: Path) -> None:
    _patch_server_windows_os(monkeypatch)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("HOMEDRIVE", raising=False)
    monkeypatch.delenv("HOMEPATH", raising=False)

    paths = server._candidate_wslconfig_paths()

    assert tmp_path / ".wslconfig" in paths


def test_systemd_keepalive_probe_is_windows_safe(monkeypatch) -> None:
    _patch_server_windows_os(monkeypatch)

    result = asyncio.run(server._inspect_systemd_keepalive())

    assert result["status"] == "unsupported"
    assert "Windows fallback" in result["detail"]


def test_systemd_timer_check_is_windows_safe(monkeypatch) -> None:
    _patch_server_windows_os(monkeypatch)

    assert server._unit_active_sync("runner-scheduler.timer") is False


def test_windows_scheduled_task_probe_uses_valid_powershell(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def fake_run_cmd(cmd, timeout=12):  # noqa: ANN001, ARG001
        captured["script"] = cmd[-1]
        return (
            0,
            json.dumps({"task_found": False, "startup_vbs_files": [], "actions": []}),
            "",
        )

    monkeypatch.setattr(server, "_resolve_powershell_executable", lambda: "powershell")
    monkeypatch.setattr(server, "run_cmd", fake_run_cmd)

    result = asyncio.run(server._inspect_windows_keepalive())

    assert result["task_found"] is False
    assert "ForEach-Object { [pscustomobject]@{ Execute =" in captured["script"]
    assert "ForEach-Object {{" not in captured["script"]
