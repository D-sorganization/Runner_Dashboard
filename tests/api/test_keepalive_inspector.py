"""Tests for diagnostics.keepalive_inspector.

All tests are fully offline — no real subprocess calls or systemd/PowerShell
access. run_cmd is monkeypatched where needed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import diagnostics.keepalive_inspector as ki  # noqa: E402
from diagnostics.keepalive_inspector import (  # noqa: E402
    KeepaliveReport,
    _detect_legacy_keepalive,
    _inspect_systemd_keepalive,
    _inspect_windows_keepalive,
    _inspect_wslconfig,
    _parse_task_action,
    _parse_vm_idle_timeout,
    _probe_detail,
)

_VALID_STATUSES = frozenset(
    {"ok", "missing", "invalid", "error", "healthy", "misconfigured", "unsupported", "unknown", "legacy"}
)

# ---------------------------------------------------------------------------
# KeepaliveReport schema
# ---------------------------------------------------------------------------

def test_keepalive_report_model_valid() -> None:
    report = KeepaliveReport(status="healthy", detail="all good")
    assert report.status == "healthy"
    assert report.source_path is None


def test_keepalive_report_with_path() -> None:
    report = KeepaliveReport(status="missing", detail="not found", source_path=Path("/tmp/x"))
    assert report.source_path == Path("/tmp/x")


def test_keepalive_report_rejects_invalid_status() -> None:
    with pytest.raises(Exception):
        KeepaliveReport(status="BAD_STATUS", detail="nope")


# ---------------------------------------------------------------------------
# _parse_vm_idle_timeout
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("[wsl2]\nvmIdleTimeout=-1\n", "-1"),
        ("[wsl2]\nvmIdleTimeout = 60\n", "60"),
        ("[wsl2]\n# vmIdleTimeout=0\nvmIdleTimeout=120\n", "120"),
        ("[wsl2]\n", None),
        ("", None),
        # Inline comment excluded by regex (# is a stop char), value stripped
        ("[wsl2]\nvmIdleTimeout=-1  # disable\n", "-1"),
    ],
)
def test_parse_vm_idle_timeout(text: str, expected: str | None) -> None:
    assert _parse_vm_idle_timeout(text) == expected


# ---------------------------------------------------------------------------
# _inspect_wslconfig (sync, uses filesystem via tmp_path)
# ---------------------------------------------------------------------------

def test_inspect_wslconfig_file_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no .wslconfig file exists, status is 'missing'."""
    monkeypatch.setattr(ki, "_candidate_wslconfig_paths", lambda: [Path("/nonexistent/.wslconfig")])
    result = _inspect_wslconfig()
    assert result["status"] == "missing"
    assert result["configured"] is False


def test_inspect_wslconfig_healthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """vmIdleTimeout=-1 → healthy."""
    cfg = tmp_path / ".wslconfig"
    cfg.write_text("[wsl2]\nvmIdleTimeout=-1\n")
    monkeypatch.setattr(ki, "_candidate_wslconfig_paths", lambda: [cfg])
    result = _inspect_wslconfig()
    assert result["status"] == "healthy"
    assert result["idle_shutdown_disabled"] is True


def test_inspect_wslconfig_misconfigured_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """vmIdleTimeout=60 (not -1) → misconfigured."""
    cfg = tmp_path / ".wslconfig"
    cfg.write_text("[wsl2]\nvmIdleTimeout=60\n")
    monkeypatch.setattr(ki, "_candidate_wslconfig_paths", lambda: [cfg])
    result = _inspect_wslconfig()
    assert result["status"] == "misconfigured"
    assert result["idle_shutdown_disabled"] is False


def test_inspect_wslconfig_no_vm_idle_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """File exists but vmIdleTimeout key absent → misconfigured."""
    cfg = tmp_path / ".wslconfig"
    cfg.write_text("[wsl2]\nmemory=4GB\n")
    monkeypatch.setattr(ki, "_candidate_wslconfig_paths", lambda: [cfg])
    result = _inspect_wslconfig()
    assert result["status"] == "misconfigured"
    assert result["vm_idle_timeout"] is None


def test_inspect_wslconfig_read_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OSError while reading → status unknown."""
    cfg = tmp_path / ".wslconfig"
    cfg.write_text("dummy")

    def _raise(_self: Path, *_a, **_kw):  # noqa: ANN001
        raise OSError("permission denied")

    monkeypatch.setattr(ki, "_candidate_wslconfig_paths", lambda: [cfg])
    monkeypatch.setattr(Path, "read_text", _raise)
    result = _inspect_wslconfig()
    assert result["status"] == "unknown"


# ---------------------------------------------------------------------------
# _inspect_systemd_keepalive (async)
# ---------------------------------------------------------------------------

async def test_inspect_systemd_keepalive_windows_os(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows (os.name == 'nt'), should return unsupported."""
    import types
    fake_os = types.SimpleNamespace(name="nt", environ=__import__("os").environ)
    monkeypatch.setattr(ki, "os", fake_os)
    result = await _inspect_systemd_keepalive()
    assert result["status"] == "unsupported"
    assert result["active"] is False


async def test_inspect_systemd_keepalive_systemd_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """systemctl returns non-zero with 'system has not been booted with systemd'."""
    async def fake_run_cmd(cmd, timeout=10):  # noqa: ANN001, ARG001
        return 1, "", "System has not been booted with systemd as init system (PID 1)."

    monkeypatch.setattr(ki, "run_cmd", fake_run_cmd)
    result = await _inspect_systemd_keepalive()
    assert result["status"] == "unsupported"


async def test_inspect_systemd_keepalive_service_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """When LoadState=loaded, ActiveState=active, UnitFileState=enabled → healthy."""
    systemctl_output = (
        "LoadState=loaded\n"
        "ActiveState=active\n"
        "UnitFileState=enabled\n"
        "FragmentPath=/etc/systemd/system/wsl-runner-keepalive.service\n"
        "Description=WSL Runner Keepalive\n"
    )

    async def fake_run_cmd(cmd, timeout=10):  # noqa: ANN001, ARG001
        return 0, systemctl_output, ""

    monkeypatch.setattr(ki, "run_cmd", fake_run_cmd)
    result = await _inspect_systemd_keepalive()
    assert result["status"] == "healthy"
    assert result["active"] is True
    assert result["enabled"] is True


async def test_inspect_systemd_keepalive_service_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Service present but inactive → misconfigured."""
    systemctl_output = (
        "LoadState=loaded\n"
        "ActiveState=inactive\n"
        "UnitFileState=disabled\n"
        "FragmentPath=/etc/systemd/system/wsl-runner-keepalive.service\n"
        "Description=WSL Runner Keepalive\n"
    )

    async def fake_run_cmd(cmd, timeout=10):  # noqa: ANN001, ARG001
        return 0, systemctl_output, ""

    monkeypatch.setattr(ki, "run_cmd", fake_run_cmd)
    result = await _inspect_systemd_keepalive()
    assert result["status"] == "misconfigured"
    assert result["active"] is False


async def test_inspect_systemd_keepalive_service_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Service not found (LoadState=not-found) → missing."""
    systemctl_output = (
        "LoadState=not-found\n"
        "ActiveState=inactive\n"
        "UnitFileState=\n"
    )

    async def fake_run_cmd(cmd, timeout=10):  # noqa: ANN001, ARG001
        return 0, systemctl_output, ""

    monkeypatch.setattr(ki, "run_cmd", fake_run_cmd)
    result = await _inspect_systemd_keepalive()
    assert result["status"] == "missing"
    assert result["configured"] is False


# ---------------------------------------------------------------------------
# _inspect_windows_keepalive (async)
# ---------------------------------------------------------------------------

async def test_inspect_windows_keepalive_no_powershell(monkeypatch: pytest.MonkeyPatch) -> None:
    """When PowerShell is not found → unsupported."""
    monkeypatch.setattr(ki, "_resolve_powershell_executable", lambda: None)
    result = await _inspect_windows_keepalive()
    assert result["status"] == "unsupported"
    assert result["task_found"] is False


async def test_inspect_windows_keepalive_task_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Task is found and Running → healthy."""
    import json

    payload = {
        "task_found": True,
        "task_name": "WSL-Runner-KeepAlive",
        "state": "Running",
        "actions": [{"Execute": "wsl.exe", "Arguments": "-d Ubuntu"}],
        "startup_vbs_files": [],
    }

    async def fake_run_cmd(cmd, timeout=12):  # noqa: ANN001, ARG001
        return 0, json.dumps(payload), ""

    monkeypatch.setattr(ki, "_resolve_powershell_executable", lambda: "powershell.exe")
    monkeypatch.setattr(ki, "run_cmd", fake_run_cmd)
    result = await _inspect_windows_keepalive()
    assert result["status"] == "healthy"
    assert result["task_found"] is True


async def test_inspect_windows_keepalive_task_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Task not found → missing."""
    import json

    payload = {
        "task_found": False,
        "task_name": "WSL-Runner-KeepAlive",
        "state": None,
        "actions": [],
        "startup_vbs_files": [],
        "error": "Task not found",
    }

    async def fake_run_cmd(cmd, timeout=12):  # noqa: ANN001, ARG001
        return 0, json.dumps(payload), ""

    monkeypatch.setattr(ki, "_resolve_powershell_executable", lambda: "powershell.exe")
    monkeypatch.setattr(ki, "run_cmd", fake_run_cmd)
    result = await _inspect_windows_keepalive()
    assert result["status"] == "missing"


async def test_inspect_windows_keepalive_legacy_vbs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy VBS file detected → legacy status."""
    import json

    payload = {
        "task_found": True,
        "task_name": "WSL-Runner-KeepAlive",
        "state": "Running",
        "actions": [{"Execute": "wscript.exe", "Arguments": "keepalive.vbs"}],
        "startup_vbs_files": ["C:\\Users\\user\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\wsl-keepalive.vbs"],
    }

    async def fake_run_cmd(cmd, timeout=12):  # noqa: ANN001, ARG001
        return 0, json.dumps(payload), ""

    monkeypatch.setattr(ki, "_resolve_powershell_executable", lambda: "powershell.exe")
    monkeypatch.setattr(ki, "run_cmd", fake_run_cmd)
    result = await _inspect_windows_keepalive()
    assert result["status"] == "legacy"
    assert result["legacy_vbs_detected"] is True


# ---------------------------------------------------------------------------
# _detect_legacy_keepalive
# ---------------------------------------------------------------------------

def test_detect_legacy_keepalive_vbs_files() -> None:
    found, detail = _detect_legacy_keepalive([], ["C:\\Users\\user\\startup\\wsl-keepalive.vbs"])
    assert found is True
    assert detail is not None


def test_detect_legacy_keepalive_wscript_action() -> None:
    actions = [{"execute": "c:\\windows\\system32\\wscript.exe", "arguments": "keepalive.vbs"}]
    found, detail = _detect_legacy_keepalive(actions, [])
    assert found is True


def test_detect_legacy_keepalive_clean() -> None:
    actions = [{"execute": "wsl.exe", "arguments": "-d Ubuntu"}]
    found, detail = _detect_legacy_keepalive(actions, [])
    assert found is False
    assert detail is None


def test_detect_legacy_keepalive_vbs_in_arguments() -> None:
    actions = [{"execute": "cmd.exe", "arguments": "keepalive.vbs /some/path"}]
    found, detail = _detect_legacy_keepalive(actions, [])
    assert found is True


# ---------------------------------------------------------------------------
# _parse_task_action
# ---------------------------------------------------------------------------

def test_parse_task_action_normalizes_keys() -> None:
    action = {"Execute": "wsl.exe", "Arguments": "-d Ubuntu"}
    result = _parse_task_action(action)
    assert result["execute"] == "wsl.exe"
    assert result["arguments"] == "-d Ubuntu"


def test_parse_task_action_handles_missing_keys() -> None:
    result = _parse_task_action({})
    assert result["execute"] is None
    assert result["arguments"] is None


# ---------------------------------------------------------------------------
# _probe_detail
# ---------------------------------------------------------------------------

def test_probe_detail_returns_detail_key() -> None:
    probe = {"detail": "things are good", "error": "ignored"}
    assert _probe_detail(probe, "fallback") == "things are good"


def test_probe_detail_falls_back_to_error() -> None:
    probe = {"error": "something broke"}
    assert _probe_detail(probe, "fallback") == "something broke"


def test_probe_detail_uses_fallback() -> None:
    assert _probe_detail({}, "default fallback") == "default fallback"


# ---------------------------------------------------------------------------
# Return value statuses conform to valid set
# ---------------------------------------------------------------------------

def test_inspect_wslconfig_status_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ki, "_candidate_wslconfig_paths", lambda: [])
    result = _inspect_wslconfig()
    assert result["status"] in _VALID_STATUSES


async def test_inspect_systemd_keepalive_status_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_cmd(cmd, timeout=10):  # noqa: ANN001, ARG001
        return 1, "", "failed to connect to bus"

    monkeypatch.setattr(ki, "run_cmd", fake_run_cmd)
    result = await _inspect_systemd_keepalive()
    assert result["status"] in _VALID_STATUSES


async def test_inspect_windows_keepalive_status_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ki, "_resolve_powershell_executable", lambda: None)
    result = await _inspect_windows_keepalive()
    assert result["status"] in _VALID_STATUSES
