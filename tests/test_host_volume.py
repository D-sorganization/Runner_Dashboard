"""Tests for host volume disk metric probing, hard floor alarms, and fleet exposure (Issue #1168).

Verifies:
1. Host volume disk probing for runner_backing_drive measuring total/used/free bytes and free percentage.
2. Hard floor alarm enforcement (< 5% or < 30 GB free) marking status=critical and scheduling_inhibited=True.
3. Prevention of conflation between guest distro-root disk metrics and host_volume.
4. Machine registry normalization and machine model serialization.
5. Fleet status API serialization and scheduling inhibition propagation.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from host_volume import (
    DEFAULT_HOST_VOLUME_MIN_FREE_GB,
    DEFAULT_HOST_VOLUME_MIN_FREE_PERCENT,
    evaluate_host_volume_alarm,
    get_host_volume_metrics,
    probe_host_volume,
    resolve_runner_backing_drive,
)


class DummyDiskUsage:
    """Mock return value for shutil.disk_usage."""

    def __init__(self, total: int, used: int, free: int) -> None:
        self.total = total
        self.used = used
        self.free = free


def test_resolve_runner_backing_drive_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit RUNNER_BACKING_DRIVE env var takes precedence."""
    monkeypatch.setenv("RUNNER_BACKING_DRIVE", "E:")
    assert resolve_runner_backing_drive() == "E:"


def test_resolve_runner_backing_drive_from_registry() -> None:
    """Resolves drive letter from machine_registry matching machine or pool name."""
    fake_registry = {
        "version": 1,
        "machines": [
            {
                "name": "ControlTower",
                "storage": {"runner_backing_drive": "F:"},
                "runner_pools": [
                    {
                        "name": "ControlTower-SSD",
                        "storage": {"runner_backing_drive": "F:"},
                    }
                ],
            },
            {
                "name": "DeskComputer",
                "storage": {"runner_backing_drive": "D:"},
            },
            {
                "name": "OGLaptop",
                "storage": {"runner_backing_drive": "C:"},
            },
        ],
    }
    assert resolve_runner_backing_drive(machine_name="ControlTower", registry=fake_registry) == "F:"
    assert resolve_runner_backing_drive(pool_name="ControlTower-SSD", registry=fake_registry) == "F:"
    assert resolve_runner_backing_drive(machine_name="DeskComputer", registry=fake_registry) == "D:"
    assert resolve_runner_backing_drive(machine_name="OGLaptop", registry=fake_registry) == "C:"


def test_resolve_runner_backing_drive_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to C: when no registry or env var is present."""
    monkeypatch.delenv("RUNNER_BACKING_DRIVE", raising=False)
    assert resolve_runner_backing_drive(registry={"machines": []}) == "C:"


def test_evaluate_host_volume_alarm_healthy() -> None:
    """Volume with plenty of space is healthy and allows scheduling."""
    # 1 TB total, 200 GB used, 800 GB free (80% free)
    total = 1000 * (1024**3)
    free = 800 * (1024**3)
    used = total - free

    metrics = evaluate_host_volume_alarm(
        total_bytes=total,
        free_bytes=free,
        drive="F:",
    )

    assert metrics["drive"] == "F:"
    assert metrics["total_bytes"] == total
    assert metrics["used_bytes"] == used
    assert metrics["free_bytes"] == free
    assert metrics["free_percent"] == 80.0
    assert metrics["percent"] == 20.0
    assert metrics["status"] == "healthy"
    assert metrics["scheduling_inhibited"] is False
    assert metrics["hard_floor_hit"] is False
    assert len(metrics["reasons"]) == 0


def test_evaluate_host_volume_alarm_incident_2026_09_05() -> None:
    """Reproduces near-miss of 2026-09-05: F: reached 3.04% free (28.3 GB of 931.5 GB).

    Under Issue #1168 this MUST trigger critical status and inhibit scheduling.
    """
    total = int(931.5 * (1024**3))
    free = int(28.3 * (1024**3))
    used = total - free

    metrics = evaluate_host_volume_alarm(
        total_bytes=total,
        free_bytes=free,
        drive="F:",
    )

    # 28.3 / 931.5 * 100 ~= 3.04%
    assert metrics["used_bytes"] == used
    assert metrics["free_percent"] < DEFAULT_HOST_VOLUME_MIN_FREE_PERCENT
    assert metrics["free_gb"] < DEFAULT_HOST_VOLUME_MIN_FREE_GB
    assert metrics["status"] == "critical"
    assert metrics["scheduling_inhibited"] is True
    assert metrics["hard_floor_hit"] is True
    assert any("hard floor" in r for r in metrics["reasons"])
    assert any("scheduling inhibited" in r.lower() for r in metrics["reasons"])


def test_evaluate_host_volume_alarm_breach_percent_only() -> None:
    """Fails closed when free percentage is below 5%, even if free_gb >= 30 GB (e.g. 10 TB drive)."""
    # 10 TB drive, 4% free = 400 GB free (> 30 GB, but < 5%)
    total = 10000 * (1024**3)
    free = 400 * (1024**3)  # 4%
    used = total - free

    metrics = evaluate_host_volume_alarm(
        total_bytes=total,
        free_bytes=free,
        drive="F:",
    )

    assert metrics["used_bytes"] == used
    assert metrics["free_percent"] == 4.0
    assert metrics["free_gb"] == 400.0
    assert metrics["status"] == "critical"
    assert metrics["scheduling_inhibited"] is True
    assert metrics["hard_floor_hit"] is True
    assert any("5.0%" in r for r in metrics["reasons"])


def test_evaluate_host_volume_alarm_breach_gb_only() -> None:
    """Fails closed when free GB is below 30 GB, even if free percentage is >= 5% (e.g. 100 GB drive)."""
    # 100 GB drive, 20 GB free = 20% free (> 5%, but < 30 GB)
    total = 100 * (1024**3)
    free = 20 * (1024**3)
    used = total - free

    metrics = evaluate_host_volume_alarm(
        total_bytes=total,
        free_bytes=free,
        drive="D:",
    )

    assert metrics["used_bytes"] == used
    assert metrics["free_percent"] == 20.0
    assert metrics["free_gb"] == 20.0
    assert metrics["status"] == "critical"
    assert metrics["scheduling_inhibited"] is True
    assert metrics["hard_floor_hit"] is True
    assert any("30.0 GB" in r for r in metrics["reasons"])


def test_probe_host_volume_custom_disk_usage() -> None:
    """probe_host_volume queries backing drive via disk_usage_fn and formats properly."""
    total = 500 * (1024**3)
    free = 150 * (1024**3)
    used = total - free
    fake_du = DummyDiskUsage(total=total, used=used, free=free)

    result = probe_host_volume(
        drive="D:",
        disk_usage_fn=lambda path: fake_du,
    )

    assert result["drive"] == "D:"
    assert result["total_bytes"] == total
    assert result["free_bytes"] == free
    assert result["used_bytes"] == used
    assert result["total_gb"] == 500.0
    assert result["free_gb"] == 150.0
    assert result["free_percent"] == 30.0
    assert result["percent"] == 70.0
    assert result["status"] == "healthy"
    assert result["scheduling_inhibited"] is False


def test_probe_host_volume_oserror_handling() -> None:
    """OSError during disk probe degrades gracefully without raising."""

    def raising_du(path: str) -> None:
        raise OSError("Drive not accessible")

    result = probe_host_volume(
        drive="Z:",
        disk_usage_fn=raising_du,
    )

    assert result["drive"] == "Z:"
    assert result["total_bytes"] == 0
    assert result["free_bytes"] == 0
    assert result["status"] == "critical"
    assert result["scheduling_inhibited"] is True
    assert any("Drive not accessible" in r for r in result["reasons"])


def test_get_host_volume_metrics_no_conflation() -> None:
    """Host volume metrics are clearly distinct and labelled."""
    metrics = get_host_volume_metrics(
        drive="F:",
        disk_usage_fn=lambda path: DummyDiskUsage(
            total=1000 * (1024**3),
            used=200 * (1024**3),
            free=800 * (1024**3),
        ),
    )
    assert "drive" in metrics
    assert "total_bytes" in metrics
    assert "used_bytes" in metrics
    assert "free_bytes" in metrics
    assert "free_percent" in metrics
    assert "percent" in metrics
    assert "status" in metrics
    assert "scheduling_inhibited" in metrics
    assert "hard_floor_hit" in metrics


def test_machine_registry_preserves_runner_backing_drive() -> None:
    """machine_registry.py normalizes and preserves runner_backing_drive."""
    from machine_registry import load_machine_registry

    reg = load_machine_registry()
    machines = {m["name"]: m for m in reg.get("machines", [])}

    assert "ControlTower" in machines
    assert "OGLaptop" in machines
    assert "DeskComputer" in machines

    # ControlTower storage and pool
    assert machines["ControlTower"]["storage"].get("runner_backing_drive") == "F:"
    ct_pools = {p["name"]: p for p in machines["ControlTower"].get("runner_pools", [])}
    active_pool = ct_pools.get("ControlTower-Runner") or ct_pools.get("ControlTower-SSD")
    assert active_pool is not None
    assert active_pool["storage"].get("runner_backing_drive") == "F:"

    # OGLaptop and DeskComputer storage
    assert machines["OGLaptop"]["storage"].get("runner_backing_drive") == "C:"
    assert machines["DeskComputer"]["storage"].get("runner_backing_drive") == "D:"


def test_resource_offline_reason_flags_host_volume_critical() -> None:
    """resource_offline_reason flags node as offline when host volume hard floor is hit."""
    from system_utils import resource_offline_reason

    system = {
        "disk": {
            "path": "/",
            "free_gb": 100.0,
            "percent": 40.0,
            "pressure": {"status": "healthy"},
            "host_volume": {
                "drive": "F:",
                "status": "critical",
                "scheduling_inhibited": True,
                "reasons": ["Host volume F: free space 3.0% is below hard floor 5.0%"],
            },
        },
        "host_volume": {
            "drive": "F:",
            "status": "critical",
            "scheduling_inhibited": True,
            "reasons": ["Host volume F: free space 3.0% is below hard floor 5.0%"],
        },
    }

    reason = resource_offline_reason(system)
    assert reason is not None
    assert reason["offline_reason"] == "host-volume-exhaustion"
    assert "3.0%" in reason["offline_detail"]
    assert reason.get("scheduling_inhibited") is True


@pytest.mark.asyncio
async def test_system_metrics_snapshot_exposes_host_volume_unconflated() -> None:
    """get_system_metrics_snapshot exposes host_volume alongside guest distro root."""
    from system_utils import get_system_metrics_snapshot

    dummy_host_vol = {
        "drive": "F:",
        "total_bytes": 1000 * (1024**3),
        "used_bytes": 950 * (1024**3),
        "free_bytes": 50 * (1024**3),
        "total_gb": 1000.0,
        "used_gb": 950.0,
        "free_gb": 50.0,
        "percent": 95.0,
        "free_percent": 5.0,
        "status": "warning",
        "scheduling_inhibited": False,
        "hard_floor_hit": False,
        "reasons": [],
    }

    with patch("system_utils.get_host_volume_metrics", return_value=dummy_host_vol):
        snapshot = await get_system_metrics_snapshot()

    assert "host_volume" in snapshot
    assert snapshot["host_volume"]["drive"] == "F:"
    assert "host_volume" in snapshot["disk"]
    assert snapshot["disk"]["host_volume"]["drive"] == "F:"

    # Verify distro root metrics remain distinct from host_volume
    assert snapshot["disk"]["path"] is not None
    assert "total_gb" in snapshot["disk"]
    assert "free_gb" in snapshot["disk"]
    # They should not be conflated
    assert isinstance(snapshot["disk"]["host_volume"], dict)


@pytest.mark.asyncio
async def test_fleet_status_serialization_with_host_volume() -> None:
    """GET /api/fleet/status includes host_volume and flags scheduling inhibition when critical."""
    from httpx import ASGITransport, AsyncClient
    from server import app

    critical_host_vol = {
        "drive": "F:",
        "total_bytes": 1000 * (1024**3),
        "used_bytes": 980 * (1024**3),
        "free_bytes": 20 * (1024**3),  # 20 GB (< 30 GB floor) -> critical
        "total_gb": 1000.0,
        "used_gb": 980.0,
        "free_gb": 20.0,
        "percent": 98.0,
        "free_percent": 2.0,
        "status": "critical",
        "scheduling_inhibited": True,
        "hard_floor_hit": True,
        "reasons": ["Host volume F: free space 2.0% is below hard floor 5.0%"],
    }

    headers = {
        "Authorization": "Bearer test-key",
        "X-Requested-With": "XMLHttpRequest",
    }
    with patch("system_utils.get_host_volume_metrics", return_value=critical_host_vol):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
        ) as ac:
            resp = await ac.get("/api/fleet/status?exclude_pools=true")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0

    # Local node in responses
    local_key = next(iter(data.keys()))
    node_data = data[local_key]

    assert "host_volume" in node_data
    assert node_data["host_volume"]["drive"] == "F:"
    assert node_data["host_volume"]["status"] == "critical"
    assert node_data["host_volume"]["scheduling_inhibited"] is True
    assert node_data.get("scheduling_inhibited") is True
    assert node_data.get("offline_reason") == "host-volume-exhaustion"
