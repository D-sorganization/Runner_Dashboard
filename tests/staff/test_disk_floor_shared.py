"""Tests for staff.disk_floor_shared (runner-scheduler-compatible disk floor)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from staff.disk_floor_shared import disk_floor_breach, staff_disk_floor_breach_reason


def test_staff_disk_floor_reason_matches_schedule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    schedule = tmp_path / "runner-schedule.json"
    schedule.write_text(
        json.dumps(
            {
                "resource_floor": {
                    "disk_path": str(tmp_path / "vol"),
                    "min_disk_free_gb": 40,
                    "readmit_disk_free_gb": 45,
                    "runners": 1,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "staff.disk_floor_shared.shutil.disk_usage",
        lambda _p: SimpleNamespace(free=35e9),
    )
    latch = tmp_path / "latch.json"
    reason = staff_disk_floor_breach_reason(config_path=schedule, latch_path=latch)
    assert reason is not None
    assert "35" in reason
    assert "40" in reason
    assert latch.is_file()


def test_disk_floor_readmit_clears_latch(tmp_path: Path) -> None:
    latch = tmp_path / "disk-floor.json"
    latch.write_text("{}", encoding="utf-8")
    floor = {
        "disk_path": str(tmp_path / "mnt"),
        "min_disk_free_gb": 40,
        "readmit_disk_free_gb": 45,
    }
    with patch("staff.disk_floor_shared.disk_free_gb", return_value=46.0):
        assert disk_floor_breach(floor, latch_path=latch) is None
    assert not latch.exists()
