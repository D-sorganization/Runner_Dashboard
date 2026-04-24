from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import server  # noqa: E402


def test_windows_wslconfig_path_is_checked_directly(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(server.os, "name", "nt")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("HOMEDRIVE", raising=False)
    monkeypatch.delenv("HOMEPATH", raising=False)

    paths = server._candidate_wslconfig_paths()

    assert tmp_path / ".wslconfig" in paths


def test_systemd_keepalive_probe_is_windows_safe(monkeypatch) -> None:
    monkeypatch.setattr(server.os, "name", "nt")

    result = asyncio.run(server._inspect_systemd_keepalive())

    assert result["status"] == "unsupported"
    assert "Windows fallback" in result["detail"]


def test_systemd_timer_check_is_windows_safe(monkeypatch) -> None:
    monkeypatch.setattr(server.os, "name", "nt")

    assert server._unit_active_sync("runner-scheduler.timer") is False


def test_windows_scheduled_task_probe_uses_valid_powershell(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def fake_run_cmd(cmd, timeout=12):  # noqa: ANN001, ARG001
        captured["script"] = cmd[-1]
        return 0, json.dumps({"task_found": False, "startup_vbs_files": [], "actions": []}), ""

    monkeypatch.setattr(server, "_resolve_powershell_executable", lambda: "powershell")
    monkeypatch.setattr(server, "run_cmd", fake_run_cmd)

    result = asyncio.run(server._inspect_windows_keepalive())

    assert result["task_found"] is False
    assert "ForEach-Object { [pscustomobject]@{ Execute =" in captured["script"]
    assert "ForEach-Object {{" not in captured["script"]
