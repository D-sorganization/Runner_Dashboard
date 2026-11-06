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
    assert not latch.is_file()


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


def test_staff_disk_probe_never_writes_latch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Staff admission must not create scheduler latch files (#4142 review)."""
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
    system_latch = tmp_path / "should-not-exist.json"
    monkeypatch.setattr(
        "staff.disk_floor_shared.shutil.disk_usage",
        lambda _p: SimpleNamespace(free=35e9),
    )
    reason = staff_disk_floor_breach_reason(config_path=schedule, latch_path=system_latch)
    assert reason is not None
    assert not system_latch.exists()


def test_staff_disk_probe_never_unlinks_scheduler_latch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When readmit applies, staff still defers worktrees but must not delete the scheduler-owned latch."""
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
    latch = tmp_path / "scheduler-latch.json"
    latch.write_text('{"from":"scheduler"}', encoding="utf-8")
    monkeypatch.setattr(
        "staff.disk_floor_shared.shutil.disk_usage",
        lambda _p: SimpleNamespace(free=44e9),
    )
    assert staff_disk_floor_breach_reason(config_path=schedule, latch_path=latch) is not None
    assert latch.is_file()


def test_manage_latch_false_skips_write_and_unlink(tmp_path: Path) -> None:
    from staff.disk_floor_shared import disk_floor_breach

    latch = tmp_path / "l.json"
    floor = {"disk_path": str(tmp_path / "d"), "min_disk_free_gb": 40, "readmit_disk_free_gb": 45}
    with patch("staff.disk_floor_shared.disk_free_gb", return_value=35.0):
        assert disk_floor_breach(floor, latch_path=latch, manage_latch=False) is not None
    assert not latch.exists()
    latch.write_text("{}", encoding="utf-8")
    with patch("staff.disk_floor_shared.disk_free_gb", return_value=46.0):
        assert disk_floor_breach(floor, latch_path=latch, manage_latch=False) is None
    assert latch.exists()
