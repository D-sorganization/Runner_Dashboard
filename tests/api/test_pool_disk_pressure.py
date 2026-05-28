"""Tests for GET /api/disk/pool-pressure (issue #754).

Verifies that the endpoint returns tier-aware pressure classifications and
correct host-disk paths for each registered runner pool.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_REGISTRY = {
    "version": 1,
    "machines": [
        {
            "name": "ControlTower",
            "aliases": [],
            "runner_pools": [
                {
                    "name": "ControlTower-NVMe",
                    "parent_machine": "ControlTower",
                    "storage_tier": "nvme",
                    "runner_base_dir": "/home/dieterolson/actions-runners-nvme",
                    "storage": {
                        "host_drive": "C:",
                        "vhdx_path": "C:\\WSL\\ext4.vhdx",
                        "disk_bus": "NVMe",
                        "disk_media_type": "SSD",
                    },
                },
                {
                    "name": "ControlTower-HDD",
                    "parent_machine": "ControlTower",
                    "storage_tier": "hdd",
                    "runner_base_dir": "/home/dieterolson/actions-runners",
                    "storage": {
                        "host_drive": "D:",
                        "vhdx_path": "D:\\WSL\\ext4.vhdx",
                        "disk_bus": "SATA",
                        "disk_media_type": "HDD",
                    },
                },
            ],
            "hardware": {},
            "workload_capacity": {},
        }
    ],
}


def _fake_disk_usage(path):
    """Return healthy disk usage for any path."""
    fake = MagicMock()
    fake.total = 500 * 1024**3
    fake.used = 200 * 1024**3
    fake.free = 300 * 1024**3
    return fake


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pool_pressure_endpoint_returns_pools(monkeypatch) -> None:
    """Endpoint returns a dict with 'pools' and 'pool_count' keys."""
    import shutil

    import metrics as metrics_module
    import system_utils

    monkeypatch.setattr(shutil, "disk_usage", _fake_disk_usage)
    monkeypatch.setattr(system_utils, "get_io_pressure_snapshot", lambda: None)

    with patch("machine_registry.load_machine_registry", return_value=_FAKE_REGISTRY):
        import asyncio

        result = asyncio.run(metrics_module.get_pool_disk_pressure())

    assert "pools" in result
    assert "pool_count" in result
    assert result["pool_count"] == 2
    assert len(result["pools"]) == 2


def test_pool_pressure_nvme_pool_has_correct_backing_disk(monkeypatch) -> None:
    """NVMe pool with host_drive='C:' maps to backing_disk='/mnt/c'."""
    import shutil

    import metrics as metrics_module
    import system_utils

    monkeypatch.setattr(shutil, "disk_usage", _fake_disk_usage)
    monkeypatch.setattr(system_utils, "get_io_pressure_snapshot", lambda: None)

    with patch("machine_registry.load_machine_registry", return_value=_FAKE_REGISTRY):
        import asyncio

        result = asyncio.run(metrics_module.get_pool_disk_pressure())

    nvme_pool = next(p for p in result["pools"] if p["pool_name"] == "ControlTower-NVMe")
    assert nvme_pool["backing_disk"] == "/mnt/c"
    assert nvme_pool["storage_tier"] == "nvme"
    assert nvme_pool["disk_bus"] == "NVMe"


def test_pool_pressure_hdd_pool_maps_to_d_drive(monkeypatch) -> None:
    """HDD pool with host_drive='D:' maps to backing_disk='/mnt/d'.

    This is the critical regression from the D: ext4.vhdx incident — the old
    code assumed /mnt/c for all pools.
    """
    import shutil

    import metrics as metrics_module
    import system_utils

    monkeypatch.setattr(shutil, "disk_usage", _fake_disk_usage)
    monkeypatch.setattr(system_utils, "get_io_pressure_snapshot", lambda: None)

    with patch("machine_registry.load_machine_registry", return_value=_FAKE_REGISTRY):
        import asyncio

        result = asyncio.run(metrics_module.get_pool_disk_pressure())

    hdd_pool = next(p for p in result["pools"] if p["pool_name"] == "ControlTower-HDD")
    assert hdd_pool["backing_disk"] == "/mnt/d"
    assert hdd_pool["storage_tier"] == "hdd"


def test_pool_pressure_includes_tier_aware_classification(monkeypatch) -> None:
    """Each pool entry includes a 'pressure' dict with tier-aware fields."""
    import shutil

    import metrics as metrics_module
    import system_utils

    monkeypatch.setattr(shutil, "disk_usage", _fake_disk_usage)
    monkeypatch.setattr(system_utils, "get_io_pressure_snapshot", lambda: None)

    with patch("machine_registry.load_machine_registry", return_value=_FAKE_REGISTRY):
        import asyncio

        result = asyncio.run(metrics_module.get_pool_disk_pressure())

    for pool in result["pools"]:
        pressure = pool["pressure"]
        assert "tier" in pressure
        assert "status" in pressure
        assert "binding_constraint" in pressure
        assert pressure["status"] in ("low", "medium", "high", "critical")


def test_pool_pressure_nvme_io_saturation_escalates(monkeypatch) -> None:
    """With high IO pressure, NVMe pool escalates to at least 'medium'."""
    import shutil

    import metrics as metrics_module
    import system_utils

    monkeypatch.setattr(shutil, "disk_usage", _fake_disk_usage)
    # Simulate high IO saturation
    monkeypatch.setattr(
        system_utils,
        "get_io_pressure_snapshot",
        lambda: {"full": {"avg10": 60.0, "avg60": 50.0, "avg300": 30.0, "total": 999}},
    )

    with patch("machine_registry.load_machine_registry", return_value=_FAKE_REGISTRY):
        import asyncio

        result = asyncio.run(metrics_module.get_pool_disk_pressure())

    nvme_pool = next(p for p in result["pools"] if p["pool_name"] == "ControlTower-NVMe")
    assert nvme_pool["pressure"]["status"] in ("medium", "high", "critical")
    assert nvme_pool["pressure"]["binding_constraint"] == "io"


def test_pool_pressure_hdd_io_saturation_does_not_cause_critical(monkeypatch) -> None:
    """With high IO pressure, HDD pool should not escalate past 'medium' on IO alone."""
    import shutil

    import metrics as metrics_module
    import system_utils

    monkeypatch.setattr(shutil, "disk_usage", _fake_disk_usage)
    monkeypatch.setattr(
        system_utils,
        "get_io_pressure_snapshot",
        lambda: {"full": {"avg10": 90.0, "avg60": 80.0, "avg300": 60.0, "total": 999}},
    )

    with patch("machine_registry.load_machine_registry", return_value=_FAKE_REGISTRY):
        import asyncio

        result = asyncio.run(metrics_module.get_pool_disk_pressure())

    hdd_pool = next(p for p in result["pools"] if p["pool_name"] == "ControlTower-HDD")
    # HDD IO alone should not go to critical — capacity must be the primary trigger
    assert hdd_pool["pressure"]["status"] in ("low", "medium")


def test_pool_pressure_empty_registry_returns_empty(monkeypatch) -> None:
    """Empty registry returns zero pools without error."""
    import shutil

    import metrics as metrics_module
    import system_utils

    monkeypatch.setattr(shutil, "disk_usage", _fake_disk_usage)
    monkeypatch.setattr(system_utils, "get_io_pressure_snapshot", lambda: None)

    empty_registry = {"version": 1, "machines": []}
    with patch("machine_registry.load_machine_registry", return_value=empty_registry):
        import asyncio

        result = asyncio.run(metrics_module.get_pool_disk_pressure())

    assert result["pools"] == []
    assert result["pool_count"] == 0
