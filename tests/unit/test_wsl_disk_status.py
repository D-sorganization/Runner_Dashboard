"""Unit tests for WSL disk status probe (issue #1332).

TDD: these tests define the expected behavior of backend/wsl_disk_status.py
before implementation.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import wsl_disk_status  # noqa: E402

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# VHDX_PS_COMMAND safety check (Read-only probe)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_vhdx_ps_command_is_strictly_read_only() -> None:
    """VHDX_PS_COMMAND must contain only read-only operations.

    Must contain none of the mutating commands or utilities.
    """
    forbidden = ["Set-", "Remove-", "Optimize-VHD", "fsutil", "wsl", "Stop-", "diskpart"]
    ps_cmd_lower = wsl_disk_status.VHDX_PS_COMMAND.lower()
    for token in forbidden:
        assert token.lower() not in ps_cmd_lower, f"Forbidden command token '{token}' found in VHDX_PS_COMMAND"


# ---------------------------------------------------------------------------
# parse_fstrim unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_fstrim_active_timer_with_timestamp() -> None:
    """Active timer with valid LastTriggerUSec and successful service result."""
    timer_out = "ActiveState=active\nLastTriggerUSec=Sun 2026-09-27 00:00:00 UTC\n"
    service_out = "Result=success\nExecMainExitTimestamp=Sun 2026-09-27 00:01:00 UTC\n"

    parsed = wsl_disk_status.parse_fstrim(timer_out, service_out)

    assert parsed["timer_active"] is True
    assert parsed["last_trigger"] == "Sun 2026-09-27 00:00:00 UTC"
    assert parsed["last_result"] == "success"


@pytest.mark.unit
def test_parse_fstrim_inactive_timer() -> None:
    """Inactive timer returns timer_active=False and preserves result."""
    timer_out = "ActiveState=inactive\nLastTriggerUSec=n/a\n"
    service_out = "Result=success\nExecMainExitTimestamp=Sun 2026-09-20 12:00:00 UTC\n"

    parsed = wsl_disk_status.parse_fstrim(timer_out, service_out)

    assert parsed["timer_active"] is False
    # When LastTriggerUSec is n/a, fallback to ExecMainExitTimestamp if available
    assert parsed["last_trigger"] == "Sun 2026-09-20 12:00:00 UTC"
    assert parsed["last_result"] == "success"


@pytest.mark.unit
def test_parse_fstrim_missing_keys() -> None:
    """Missing keys or empty output map to all None."""
    parsed = wsl_disk_status.parse_fstrim("", "")

    assert parsed == {"timer_active": None, "last_trigger": None, "last_result": None}


@pytest.mark.unit
def test_parse_fstrim_na_and_zero_values() -> None:
    """Values equal to 'n/a', '0', or empty strings map to None."""
    timer_out = "ActiveState=n/a\nLastTriggerUSec=0\n"
    service_out = "Result=n/a\nExecMainExitTimestamp=0\n"

    parsed = wsl_disk_status.parse_fstrim(timer_out, service_out)

    assert parsed["timer_active"] is None
    assert parsed["last_trigger"] is None
    assert parsed["last_result"] is None


@pytest.mark.unit
def test_parse_fstrim_non_active_state_is_false() -> None:
    """Any state other than 'active' (e.g. failed, deactivating) yields timer_active=False."""
    timer_out = "ActiveState=failed\nLastTriggerUSec=Sun 2026-09-27 00:00:00 UTC\n"
    service_out = "Result=failed\nExecMainExitTimestamp=Sun 2026-09-27 00:01:00 UTC\n"

    parsed = wsl_disk_status.parse_fstrim(timer_out, service_out)

    assert parsed["timer_active"] is False
    assert parsed["last_result"] == "failed"


# ---------------------------------------------------------------------------
# build_disk_status unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_disk_status_sparse_false_reports_not_sparse() -> None:
    """When a distribution has sparse=False, 'not_sparse' is added to findings."""
    vhdx_files = [
        {"Distribution": "Ubuntu", "Path": "C:\\wsl\\ext4.vhdx", "Bytes": 50_000_000, "Sparse": False},
    ]
    fstrim = {"timer_active": True, "last_trigger": "timestamp", "last_result": "success"}

    status = wsl_disk_status.build_disk_status(
        vhdx_files,
        current_distro="Ubuntu",
        fs_used_bytes=20_000_000,
        fs_total_bytes=100_000_000,
        fstrim=fstrim,
    )

    dist = status["distributions"][0]
    assert dist["name"] == "Ubuntu"
    assert dist["sparse"] is False
    assert "not_sparse" in dist["findings"]
    assert "fstrim_timer_inactive" not in dist["findings"]


@pytest.mark.unit
def test_build_disk_status_current_distro_inactive_timer_reports_fstrim_timer_inactive() -> None:
    """Current distro with timer_active=False receives 'fstrim_timer_inactive'."""
    vhdx_files = [
        {"Distribution": "Ubuntu", "Path": "C:\\wsl\\ext4.vhdx", "Bytes": 50_000_000, "Sparse": True},
    ]
    fstrim = {"timer_active": False, "last_trigger": None, "last_result": None}

    status = wsl_disk_status.build_disk_status(
        vhdx_files,
        current_distro="Ubuntu",
        fs_used_bytes=20_000_000,
        fs_total_bytes=100_000_000,
        fstrim=fstrim,
    )

    dist = status["distributions"][0]
    assert dist["is_current"] is True
    assert "fstrim_timer_inactive" in dist["findings"]
    assert "not_sparse" not in dist["findings"]


@pytest.mark.unit
def test_build_disk_status_non_current_distro_no_fs_and_no_timer_finding() -> None:
    """Non-current distros must not have filesystem numbers or a fstrim timer finding."""
    vhdx_files = [
        {"Distribution": "Ubuntu", "Path": "C:\\wsl\\ubuntu.vhdx", "Bytes": 50_000_000, "Sparse": True},
        {"Distribution": "Debian", "Path": "C:\\wsl\\debian.vhdx", "Bytes": 40_000_000, "Sparse": False},
    ]
    fstrim = {"timer_active": False, "last_trigger": None, "last_result": None}

    status = wsl_disk_status.build_disk_status(
        vhdx_files,
        current_distro="Ubuntu",
        fs_used_bytes=20_000_000,
        fs_total_bytes=100_000_000,
        fstrim=fstrim,
    )

    ubuntu = next(d for d in status["distributions"] if d["name"] == "Ubuntu")
    assert ubuntu["is_current"] is True
    assert ubuntu["fs_used_bytes"] == 20_000_000
    assert ubuntu["fs_total_bytes"] == 100_000_000
    assert "fstrim_timer_inactive" in ubuntu["findings"]

    debian = next(d for d in status["distributions"] if d["name"] == "Debian")
    assert debian["is_current"] is False
    assert debian["fs_used_bytes"] is None
    assert debian["fs_total_bytes"] is None
    assert debian["slack_bytes"] is None
    assert "fstrim_timer_inactive" not in debian["findings"]
    # Sparse is False so it still reports not_sparse
    assert "not_sparse" in debian["findings"]


@pytest.mark.unit
def test_build_disk_status_slack_computed_and_clamped_at_zero() -> None:
    """Slack is vhdx_bytes - fs_used_bytes when positive, clamped to 0 when negative/zero."""
    fstrim = {"timer_active": True, "last_trigger": None, "last_result": None}

    # Case 1: Positive slack
    vhdx_pos = [{"Distribution": "Ubuntu", "Path": "C:\\path", "Bytes": 100_000_000, "Sparse": True}]
    status_pos = wsl_disk_status.build_disk_status(
        vhdx_pos,
        current_distro="Ubuntu",
        fs_used_bytes=40_000_000,
        fs_total_bytes=200_000_000,
        fstrim=fstrim,
    )
    assert status_pos["distributions"][0]["slack_bytes"] == 60_000_000

    # Case 2: Negative difference clamped to 0
    vhdx_neg = [{"Distribution": "Ubuntu", "Path": "C:\\path", "Bytes": 30_000_000, "Sparse": True}]
    status_neg = wsl_disk_status.build_disk_status(
        vhdx_neg,
        current_distro="Ubuntu",
        fs_used_bytes=40_000_000,
        fs_total_bytes=200_000_000,
        fstrim=fstrim,
    )
    assert status_neg["distributions"][0]["slack_bytes"] == 0

    # Case 3: One or both unknown yields None
    vhdx_none = [{"Distribution": "Ubuntu", "Path": "C:\\path", "Bytes": 100_000_000, "Sparse": True}]
    status_none = wsl_disk_status.build_disk_status(
        vhdx_none,
        current_distro="Ubuntu",
        fs_used_bytes=None,
        fs_total_bytes=None,
        fstrim=fstrim,
    )
    assert status_none["distributions"][0]["slack_bytes"] is None


@pytest.mark.unit
def test_build_disk_status_accepts_lowercase_keys() -> None:
    """Accepts lowercase keys ('distribution', 'path', 'bytes', 'sparse')."""
    vhdx_files = [
        {"distribution": "Arch", "path": "C:\\wsl\\arch.vhdx", "bytes": 80_000_000, "sparse": True},
    ]
    fstrim = {"timer_active": True, "last_trigger": None, "last_result": None}

    status = wsl_disk_status.build_disk_status(
        vhdx_files,
        current_distro="Arch",
        fs_used_bytes=30_000_000,
        fs_total_bytes=100_000_000,
        fstrim=fstrim,
    )

    dist = status["distributions"][0]
    assert dist["name"] == "Arch"
    assert dist["path"] == "C:\\wsl\\arch.vhdx"
    assert dist["vhdx_bytes"] == 80_000_000
    assert dist["sparse"] is True
    assert dist["is_current"] is True
    assert dist["slack_bytes"] == 50_000_000


@pytest.mark.unit
def test_build_disk_status_negative_fs_used_bytes_raises_assertion_error() -> None:
    """Precondition check: negative fs_used_bytes raises AssertionError."""
    vhdx_files = [{"Distribution": "Ubuntu", "Path": "C:\\path", "Bytes": 100, "Sparse": True}]
    fstrim = {"timer_active": True, "last_trigger": None, "last_result": None}

    with pytest.raises(AssertionError):
        wsl_disk_status.build_disk_status(
            vhdx_files,
            current_distro="Ubuntu",
            fs_used_bytes=-10,
            fs_total_bytes=100,
            fstrim=fstrim,
        )


@pytest.mark.unit
def test_build_disk_status_negative_fs_total_bytes_raises_assertion_error() -> None:
    """Precondition check: negative fs_total_bytes raises AssertionError."""
    vhdx_files = [{"Distribution": "Ubuntu", "Path": "C:\\path", "Bytes": 100, "Sparse": True}]
    fstrim = {"timer_active": True, "last_trigger": None, "last_result": None}

    with pytest.raises(AssertionError):
        wsl_disk_status.build_disk_status(
            vhdx_files,
            current_distro="Ubuntu",
            fs_used_bytes=10,
            fs_total_bytes=-50,
            fstrim=fstrim,
        )


# ---------------------------------------------------------------------------
# query_vhdx_files unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_query_vhdx_files_returns_empty_when_powershell_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When shutil.which returns None for PowerShell, query_vhdx_files returns []."""
    monkeypatch.setattr(shutil, "which", lambda *_: None)

    result = await wsl_disk_status.query_vhdx_files()
    assert result == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_query_vhdx_files_parses_single_object_json_into_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PowerShell ConvertTo-Json emits a single dict when only one distro exists; becomes a list."""
    single_obj: dict[str, Any] = {
        "Distribution": "Ubuntu",
        "Path": "C:\\WSL\\ext4.vhdx",
        "Bytes": 10737418240,
        "Sparse": True,
    }
    fake_proc = subprocess.CompletedProcess(
        args=["powershell", "-Command", "dummy"],
        returncode=0,
        stdout=json.dumps(single_obj),
        stderr="",
    )

    monkeypatch.setattr(shutil, "which", lambda cmd: "/fake/powershell.exe")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_proc)

    result = await wsl_disk_status.query_vhdx_files()
    assert len(result) == 1
    assert result[0]["Distribution"] == "Ubuntu"
    assert result[0]["Bytes"] == 10737418240
    assert result[0]["Sparse"] is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_query_vhdx_files_parses_list_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PowerShell ConvertTo-Json emits a list when multiple distros exist."""
    items = [
        {"Distribution": "Ubuntu", "Path": "C:\\WSL\\u.vhdx", "Bytes": 1000, "Sparse": True},
        {"Distribution": "Debian", "Path": "C:\\WSL\\d.vhdx", "Bytes": 2000, "Sparse": False},
    ]
    fake_proc = subprocess.CompletedProcess(
        args=["powershell", "-Command", "dummy"],
        returncode=0,
        stdout=json.dumps(items),
        stderr="",
    )

    monkeypatch.setattr(shutil, "which", lambda cmd: "/fake/powershell.exe")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_proc)

    result = await wsl_disk_status.query_vhdx_files()
    assert len(result) == 2
    assert result[0]["Distribution"] == "Ubuntu"
    assert result[1]["Distribution"] == "Debian"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_query_vhdx_files_returns_empty_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid JSON stdout is caught and handled gracefully, returning []."""
    fake_proc = subprocess.CompletedProcess(
        args=["powershell", "-Command", "dummy"],
        returncode=0,
        stdout="Error: this is not json { [",
        stderr="",
    )

    monkeypatch.setattr(shutil, "which", lambda cmd: "/fake/powershell.exe")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_proc)

    result = await wsl_disk_status.query_vhdx_files()
    assert result == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_query_vhdx_files_returns_empty_on_nonzero_exit_or_empty_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-zero exit code or empty stdout returns []."""
    monkeypatch.setattr(shutil, "which", lambda cmd: "/fake/powershell.exe")

    # Non-zero exit code
    fake_fail = subprocess.CompletedProcess(args=["powershell"], returncode=1, stdout="", stderr="some error")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_fail)
    assert await wsl_disk_status.query_vhdx_files() == []

    # Empty stdout
    fake_empty = subprocess.CompletedProcess(args=["powershell"], returncode=0, stdout="   \n", stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_empty)
    assert await wsl_disk_status.query_vhdx_files() == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_query_vhdx_files_returns_empty_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exceptions raised during execution return [] without crashing."""
    monkeypatch.setattr(shutil, "which", lambda cmd: "/fake/powershell.exe")

    def _broken_run(*_a: Any, **_kw: Any) -> Any:
        raise OSError("Process failed to start")

    monkeypatch.setattr(subprocess, "run", _broken_run)
    assert await wsl_disk_status.query_vhdx_files() == []


# ---------------------------------------------------------------------------
# query_fstrim unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_query_fstrim_returns_all_none_when_systemctl_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When systemctl is not found on PATH, all fields in fstrim are None."""
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    result = wsl_disk_status.query_fstrim()
    assert result == {"timer_active": None, "last_trigger": None, "last_result": None}


@pytest.mark.unit
def test_query_fstrim_returns_parsed_when_systemctl_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When systemctl runs, output is passed through parse_fstrim."""
    monkeypatch.setattr(shutil, "which", lambda cmd: "/bin/systemctl")

    def _mock_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "fstrim.timer" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="ActiveState=active\nLastTriggerUSec=Sun 2026-09-27 00:00:00 UTC\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="Result=success\nExecMainExitTimestamp=Sun 2026-09-27 00:01:00 UTC\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _mock_run)

    result = wsl_disk_status.query_fstrim()
    assert result["timer_active"] is True
    assert result["last_trigger"] == "Sun 2026-09-27 00:00:00 UTC"
    assert result["last_result"] == "success"


@pytest.mark.unit
def test_query_fstrim_never_raises_on_command_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """query_fstrim never raises even if subprocess.run fails."""
    monkeypatch.setattr(shutil, "which", lambda cmd: "/bin/systemctl")

    mock_run = MagicMock(side_effect=subprocess.TimeoutExpired(cmd=["systemctl"], timeout=5))
    monkeypatch.setattr(subprocess, "run", mock_run)

    result = wsl_disk_status.query_fstrim()
    assert result == {"timer_active": None, "last_trigger": None, "last_result": None}
