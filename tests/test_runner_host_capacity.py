"""Regression tests for #4307: per-host caps, resource_floor preservation, drift."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import runner_host_capacity  # noqa: E402
import server  # noqa: E402


def test_resource_floor_round_trips_on_validate() -> None:
    floor = {
        "disk_path": "/mnt/c",
        "min_disk_free_gb": 40,
        "min_mem_available_gb": 6,
        "max_memory_pressure_avg60": 20,
        "runners": 1,
    }
    payload = {
        "enabled": True,
        "timezone": "America/Los_Angeles",
        "default_count": 2,
        "max_count": 4,
        "schedules": [],
        "resource_floor": floor,
    }
    result = server._validate_runner_schedule(payload)
    assert result["resource_floor"] == floor


@pytest.mark.parametrize(
    ("hostname", "aliases", "requested", "expected"),
    [
        ("hub-a", [], 16, 6),
        ("node-b", [], 12, 6),
        ("node-a", [], 10, 8),
    ],
)
def test_host_cap_clamps_max_count(hostname: str, aliases: list[str], requested: int, expected: int) -> None:
    sanitized, notices = server._sanitize_runner_schedule(
        {
            "enabled": True,
            "max_count": requested,
            "default_count": 1,
            "schedules": [
                {
                    "name": "peak",
                    "days": ["mon"],
                    "start": "08:00",
                    "end": "17:00",
                    "runners": requested,
                }
            ],
        },
        hostname=hostname,
        aliases=aliases,
    )
    assert sanitized["max_count"] == expected
    assert sanitized["schedules"][0]["runners"] == expected
    assert any("clamped" in note for note in notices)


def test_schedule_config_drift_detects_live_max_count_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seed = {
        "enabled": True,
        "default_count": 1,
        "max_count": 6,
        "schedules": [],
    }
    live = dict(seed)
    live["max_count"] = 16
    drift = runner_host_capacity.schedule_config_drift(
        live,
        hostname="hub-a",
        aliases=[],
        live_path=tmp_path / "live.json",
    )
    assert drift["checked"] is True
    assert drift["drifted"] is True
    assert drift["live_max_count"] == 16
    assert drift["seed_max_count"] == 6


def test_capacity_snapshot_includes_hard_cap_and_drift(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seed_path = Path(__file__).resolve().parent.parent / "config" / "runner-schedule.json"
    schedule = json.loads(seed_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(server, "HOSTNAME", "hub-a")
    monkeypatch.setattr(server, "RUNNER_ALIASES", [])
    monkeypatch.setattr(server, "RUNNER_BASE_DIR", tmp_path)
    monkeypatch.setattr(server, "_load_runner_schedule_config", lambda: schedule)
    monkeypatch.setattr(server, "_sync_runner_scheduler_state", lambda config: {"config": config})
    monkeypatch.setattr(server, "_unit_active_sync", lambda _unit: False)
    monkeypatch.setattr(server, "_runner_limit", lambda: 16)

    snapshot = server._build_runner_capacity_snapshot()

    assert snapshot["host_runner_hard_cap"] == 6
    assert snapshot["schedule_config_drift"]["checked"] is True
    assert snapshot["schedule_config_drift"]["drifted"] is False
