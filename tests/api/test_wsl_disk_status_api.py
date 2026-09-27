"""Tests for /api/diagnostics/wsl-disk endpoint (issue #1332).

Covers:
  GET /api/diagnostics/wsl-disk — read-only status view of WSL VHDX files,
  sparse flags, filesystem slack space, and fstrim timer status.

TDD: these tests are written before the implementation.
"""

from __future__ import annotations

import shutil
import sys
from collections import namedtuple
from pathlib import Path
from typing import Any

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import wsl_disk_status  # noqa: E402
from routers import diagnostics as diagnostics_router  # noqa: E402

_DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])


# ---------------------------------------------------------------------------
# /api/diagnostics/wsl-disk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wsl_disk_endpoint_response_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint must return distributions, fstrim, current_distro, and generated_at."""

    async def _mock_vhdx() -> list[dict[str, Any]]:
        return [
            {
                "Distribution": "Ubuntu",
                "Path": "C:\\WSL\\ext4.vhdx",
                "Bytes": 100_000_000_000,
                "Sparse": True,
            }
        ]

    def _mock_fstrim() -> dict[str, Any]:
        return {
            "timer_active": True,
            "last_trigger": "Sun 2026-09-27 00:00:00 UTC",
            "last_result": "success",
        }

    usage = _DiskUsage(total=200_000_000_000, used=40_000_000_000, free=160_000_000_000)
    monkeypatch.setattr(wsl_disk_status, "query_vhdx_files", _mock_vhdx)
    monkeypatch.setattr(wsl_disk_status, "query_fstrim", _mock_fstrim)
    monkeypatch.setattr(shutil, "disk_usage", lambda _: usage)
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")

    result = await diagnostics_router.get_wsl_disk_status()

    assert isinstance(result, dict)
    assert "distributions" in result
    assert "fstrim" in result
    assert "current_distro" in result
    assert "generated_at" in result

    assert result["current_distro"] == "Ubuntu"
    assert result["fstrim"]["timer_active"] is True

    assert len(result["distributions"]) == 1
    dist = result["distributions"][0]
    assert dist["name"] == "Ubuntu"
    assert dist["path"] == "C:\\WSL\\ext4.vhdx"
    assert dist["vhdx_bytes"] == 100_000_000_000
    assert dist["sparse"] is True
    assert dist["is_current"] is True
    assert dist["fs_used_bytes"] == 40_000_000_000
    assert dist["fs_total_bytes"] == 200_000_000_000
    assert dist["slack_bytes"] == 60_000_000_000
    assert dist["findings"] == []


@pytest.mark.asyncio
async def test_wsl_disk_endpoint_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint reports not_sparse when sparse=False and fstrim_timer_inactive when the timer is inactive."""

    async def _mock_vhdx() -> list[dict[str, Any]]:
        return [
            {
                "Distribution": "Ubuntu-22.04",
                "Path": "C:\\WSL\\u22.vhdx",
                "Bytes": 80_000_000_000,
                "Sparse": False,
            },
            {
                "Distribution": "Debian",
                "Path": "C:\\WSL\\debian.vhdx",
                "Bytes": 50_000_000_000,
                "Sparse": True,
            },
        ]

    def _mock_fstrim() -> dict[str, Any]:
        return {
            "timer_active": False,
            "last_trigger": None,
            "last_result": None,
        }

    usage = _DiskUsage(total=100_000_000_000, used=30_000_000_000, free=70_000_000_000)
    monkeypatch.setattr(wsl_disk_status, "query_vhdx_files", _mock_vhdx)
    monkeypatch.setattr(wsl_disk_status, "query_fstrim", _mock_fstrim)
    monkeypatch.setattr(shutil, "disk_usage", lambda _: usage)
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-22.04")

    result = await diagnostics_router.get_wsl_disk_status()

    dists = {d["name"]: d for d in result["distributions"]}

    # Current distro: sparse is False, fstrim timer is False -> both findings items
    current_entry = dists["Ubuntu-22.04"]
    assert current_entry["is_current"] is True
    assert "not_sparse" in current_entry["findings"]
    assert "fstrim_timer_inactive" in current_entry["findings"]
    assert current_entry["slack_bytes"] == 50_000_000_000

    # Non-current distro: sparse is True -> no not_sparse; not current -> no timer finding
    debian_entry = dists["Debian"]
    assert debian_entry["is_current"] is False
    assert debian_entry["findings"] == []
    assert debian_entry["fs_used_bytes"] is None
    assert debian_entry["fs_total_bytes"] is None
    assert debian_entry["slack_bytes"] is None


@pytest.mark.asyncio
async def test_wsl_disk_endpoint_without_wsl_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When running outside WSL (WSL_DISTRO_NAME unset), fs usage is skipped gracefully."""

    async def _mock_vhdx() -> list[dict[str, Any]]:
        return [
            {
                "Distribution": "Ubuntu",
                "Path": "C:\\WSL\\ext4.vhdx",
                "Bytes": 100_000_000,
                "Sparse": True,
            }
        ]

    def _mock_fstrim() -> dict[str, Any]:
        return {"timer_active": False, "last_trigger": None, "last_result": None}

    monkeypatch.setattr(wsl_disk_status, "query_vhdx_files", _mock_vhdx)
    monkeypatch.setattr(wsl_disk_status, "query_fstrim", _mock_fstrim)
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)

    result = await diagnostics_router.get_wsl_disk_status()

    assert result["current_distro"] is None
    dist = result["distributions"][0]
    assert dist["is_current"] is False
    assert dist["fs_used_bytes"] is None
    assert dist["fs_total_bytes"] is None
    assert dist["slack_bytes"] is None
    assert "fstrim_timer_inactive" not in dist["findings"]
