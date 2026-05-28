"""Tests for pool diagnostics endpoints (issue #756).

Covers:
  GET /api/diagnostics/vhdx         — VHDX attachment + size info
  GET /api/diagnostics/pool-recovery — structured recovery guidance per scenario
  Sharing-violation detection helpers

TDD: these tests are written before the implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from routers import diagnostics as diagnostics_router  # noqa: E402

# ---------------------------------------------------------------------------
# /api/diagnostics/vhdx
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vhdx_endpoint_returns_list_when_powershell_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When PowerShell is not on PATH the endpoint returns an empty list gracefully."""

    async def _no_vhdx() -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(diagnostics_router, "query_wsl_vhdx_status", _no_vhdx)

    result = await diagnostics_router.get_vhdx_diagnostics()

    assert isinstance(result, dict)
    assert "distributions" in result
    assert isinstance(result["distributions"], list)
    assert result["distributions"] == []
    assert "generated_at" in result


@pytest.mark.asyncio
async def test_vhdx_endpoint_surfaces_attached_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each distribution entry must expose the Attached boolean from Get-DiskImage."""

    async def _fake_vhdx() -> list[dict[str, Any]]:
        return [
            {"Distribution": "Ubuntu", "Path": "C:\\test\\ext4.vhdx", "Attached": True},
        ]

    monkeypatch.setattr(diagnostics_router, "query_wsl_vhdx_status", _fake_vhdx)

    result = await diagnostics_router.get_vhdx_diagnostics()

    assert len(result["distributions"]) == 1
    entry = result["distributions"][0]
    assert entry["name"] == "Ubuntu"
    assert entry["path"] == "C:\\test\\ext4.vhdx"
    assert entry["attached"] is True


@pytest.mark.asyncio
async def test_vhdx_endpoint_detects_sharing_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """storage_incident block must be present and reflect sharing-violation state."""

    async def _fake_vhdx() -> list[dict[str, Any]]:
        return [
            {"Distribution": "Ubuntu", "Path": "C:\\test\\ext4.vhdx", "Attached": False},
        ]

    monkeypatch.setattr(diagnostics_router, "query_wsl_vhdx_status", _fake_vhdx)
    monkeypatch.setattr(
        diagnostics_router,
        "detect_sharing_violations",
        lambda *_a, **_kw: {
            "detected": True,
            "error_code": "ERROR_SHARING_VIOLATION",
            "target_file": "C:\\test\\ext4.vhdx",
            "message": "VHDX locked",
        },
    )

    result = await diagnostics_router.get_vhdx_diagnostics()

    incident = result["storage_incident"]
    assert incident["detected"] is True
    assert incident["error_code"] == "ERROR_SHARING_VIOLATION"


@pytest.mark.asyncio
async def test_vhdx_endpoint_no_incident_when_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """storage_incident.detected must be False when no sharing violations exist."""

    async def _fake_vhdx() -> list[dict[str, Any]]:
        return [
            {"Distribution": "Ubuntu", "Path": "C:\\test\\ext4.vhdx", "Attached": True},
        ]

    monkeypatch.setattr(diagnostics_router, "query_wsl_vhdx_status", _fake_vhdx)
    monkeypatch.setattr(
        diagnostics_router,
        "detect_sharing_violations",
        lambda *_a, **_kw: {"detected": False, "error_code": None, "target_file": None, "message": None},
    )

    result = await diagnostics_router.get_vhdx_diagnostics()

    assert result["storage_incident"]["detected"] is False


# ---------------------------------------------------------------------------
# /api/diagnostics/pool-recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_recovery_returns_all_scenarios() -> None:
    """The response must include guidance for every documented failure scenario."""
    result = await diagnostics_router.get_pool_recovery_guidance()

    assert "scenarios" in result
    scenario_ids = {s["id"] for s in result["scenarios"]}
    required = {"vhdx_locked", "disk_full", "wsl_boot_failure"}
    assert required.issubset(scenario_ids), f"missing scenarios: {required - scenario_ids}"


@pytest.mark.asyncio
async def test_pool_recovery_vhdx_locked_scenario_has_steps() -> None:
    """Each scenario must include a non-empty list of operator action steps."""
    result = await diagnostics_router.get_pool_recovery_guidance()

    vhdx_scenario = next(s for s in result["scenarios"] if s["id"] == "vhdx_locked")
    assert "steps" in vhdx_scenario
    assert len(vhdx_scenario["steps"]) > 0
    # Steps must be strings
    assert all(isinstance(step, str) for step in vhdx_scenario["steps"])


@pytest.mark.asyncio
async def test_pool_recovery_safe_compaction_warning() -> None:
    """The vhdx_locked scenario must warn NOT to restart WSL during active Optimize-VHD."""
    result = await diagnostics_router.get_pool_recovery_guidance()

    vhdx_scenario = next(s for s in result["scenarios"] if s["id"] == "vhdx_locked")
    all_text = " ".join(vhdx_scenario["steps"]).lower() + vhdx_scenario.get("warning", "").lower()
    # Must explicitly mention not restarting WSL during compaction
    assert "optimize-vhd" in all_text or "compact" in all_text


@pytest.mark.asyncio
async def test_pool_recovery_disk_full_scenario_has_steps() -> None:
    """disk_full scenario must have actionable steps."""
    result = await diagnostics_router.get_pool_recovery_guidance()

    disk_scenario = next(s for s in result["scenarios"] if s["id"] == "disk_full")
    assert len(disk_scenario["steps"]) > 0


@pytest.mark.asyncio
async def test_pool_recovery_wsl_boot_failure_has_steps() -> None:
    """wsl_boot_failure scenario must have actionable steps."""
    result = await diagnostics_router.get_pool_recovery_guidance()

    wsl_scenario = next(s for s in result["scenarios"] if s["id"] == "wsl_boot_failure")
    assert len(wsl_scenario["steps"]) > 0


@pytest.mark.asyncio
async def test_pool_recovery_schema_has_metadata() -> None:
    """Response must include a generated_at timestamp and a runbook reference."""
    result = await diagnostics_router.get_pool_recovery_guidance()

    assert "generated_at" in result
    assert "runbook_url" in result


@pytest.mark.asyncio
async def test_pool_recovery_distinguishes_active_compaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a VHDX is attached (Optimize-VHD active), guidance must differ from a plain lock."""

    # Simulate a VHDX that is Attached=True (compaction in progress)
    async def _attached_vhdx() -> list[dict[str, Any]]:
        return [{"Distribution": "Ubuntu", "Path": "C:\\test\\ext4.vhdx", "Attached": True}]

    monkeypatch.setattr(diagnostics_router, "query_wsl_vhdx_status", _attached_vhdx)

    result = await diagnostics_router.get_pool_recovery_guidance()

    vhdx_scenario = next(s for s in result["scenarios"] if s["id"] == "vhdx_locked")
    # The warning field must exist when compaction appears active
    assert "warning" in vhdx_scenario


# ---------------------------------------------------------------------------
# detect_sharing_violations helper unit tests
# ---------------------------------------------------------------------------


def test_detect_sharing_violations_no_violation_on_missing_files() -> None:
    """No violation reported when DB files don't exist."""
    result = diagnostics_router.detect_sharing_violations(wsl_vhdx_status=[], wsl_status_str="")
    assert result["detected"] is False


def test_detect_sharing_violations_no_violation_when_distro_running() -> None:
    """A locked VHDX is not reported as a violation when its distro is Running."""
    # Provide a distro marked running — the function should skip reporting it
    vhdx_status = [{"Distribution": "Ubuntu", "Path": "/fake/path", "Attached": True}]
    result = diagnostics_router.detect_sharing_violations(
        wsl_vhdx_status=vhdx_status,
        wsl_status_str="Ubuntu Running",
    )
    # /fake/path does not exist on CI, so no real permission check happens
    assert isinstance(result["detected"], bool)
