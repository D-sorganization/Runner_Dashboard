"""RD-OPS-017 disk-full cordon (issue #2030)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from disk_full_cordon import (
    CordonActionKind,
    DiskFullCordonState,
    RunnerSnapshot,
    disk_pressure_allows_readmit,
    disk_pressure_trips,
    multi_host_cordon_alert_needed,
    plan_cordon_tick,
)
from rm_ops_017_config import (
    DiskFullCordonConfig,
    DiskFullCordonReadmitThresholds,
    DiskFullCordonTripThresholds,
    load_disk_full_cordon_config,
    parse_disk_full_cordon_config,
)


def _cfg() -> DiskFullCordonConfig:
    return DiskFullCordonConfig(
        enabled=True,
        trip=DiskFullCordonTripThresholds(free_percent_below=5.0, free_gb_below=30.0),
        readmit=DiskFullCordonReadmitThresholds(free_percent_above=10.0, free_gb_above=50.0),
        drain_timeout_seconds=120.0,
        multi_host_cordon_alert_min=2,
    )


def test_disk_pressure_trips_on_percent_or_gb() -> None:
    trip = _cfg().trip
    assert disk_pressure_trips(40.0, 4.0, trip) is True
    assert disk_pressure_trips(25.0, 20.0, trip) is True
    assert disk_pressure_trips(40.0, 20.0, trip) is False


def test_readmit_requires_both_thresholds() -> None:
    readmit = _cfg().readmit
    assert disk_pressure_allows_readmit(55.0, 12.0, readmit) is True
    assert disk_pressure_allows_readmit(55.0, 8.0, readmit) is False
    assert disk_pressure_allows_readmit(45.0, 12.0, readmit) is False


def test_trip_starts_drain_and_sets_min_online_zero() -> None:
    state = DiskFullCordonState()
    runners = [
        RunnerSnapshot("u1", active=True, busy=True),
        RunnerSnapshot("u2", active=True, busy=False),
    ]
    result = plan_cordon_tick(
        state,
        free_gb=20.0,
        free_percent=4.0,
        config=_cfg(),
        runners=runners,
        now=1000.0,
    )
    assert result.state.phase == "tripping"
    assert result.effective_min_online == 0
    assert result.scheduling_inhibited is True
    kinds = {a.kind for a in result.actions}
    assert CordonActionKind.SET_MAINTENANCE_ON in kinds
    stops = [a for a in result.actions if a.kind == CordonActionKind.STOP_RUNNER]
    assert len(stops) == 1
    assert stops[0].unit == "u2"
    assert stops[0].force is False


def test_drain_timeout_force_stops_busy_runners() -> None:
    state = DiskFullCordonState(phase="tripping", tripped_at=1000.0)
    runners = [RunnerSnapshot("u1", active=True, busy=True)]
    result = plan_cordon_tick(
        state,
        free_gb=20.0,
        free_percent=4.0,
        config=_cfg(),
        runners=runners,
        now=1125.0,
    )
    stop = next(a for a in result.actions if a.kind == CordonActionKind.STOP_RUNNER)
    assert stop.force is True


def test_cordoned_runs_cleanup_once_then_readmits_with_hysteresis() -> None:
    state = DiskFullCordonState(phase="cordoned", tripped_at=900.0, cleanup_done=False)
    low = plan_cordon_tick(
        state,
        free_gb=55.0,
        free_percent=12.0,
        config=_cfg(),
        runners=[],
        now=2000.0,
    )
    assert any(a.kind == CordonActionKind.RUN_DISK_CLEANUP for a in low.actions)
    assert low.state.phase == "cordoned"
    assert low.state.cleanup_done is True

    cleared = plan_cordon_tick(
        low.state,
        free_gb=55.0,
        free_percent=12.0,
        config=_cfg(),
        runners=[],
        now=2001.0,
    )
    assert cleared.state.phase == "normal"
    assert any(a.kind == CordonActionKind.SET_MAINTENANCE_OFF for a in cleared.actions)


def test_multi_host_cordon_alert_fires_at_threshold() -> None:
    assert multi_host_cordon_alert_needed(None, 2, 2) is True
    assert multi_host_cordon_alert_needed(1, 2, 2) is True
    assert multi_host_cordon_alert_needed(2, 2, 2) is False


def test_load_config_from_repo_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "rm-ops-017.json"
    cfg_path.write_text(
        json.dumps(
            {
                "disk_full_cordon": {
                    "enabled": True,
                    "trip": {"free_percent_below": 6.0, "free_gb_below": 31.0},
                    "readmit": {"free_percent_above": 11.0, "free_gb_above": 51.0},
                    "drain_timeout_seconds": 600,
                    "multi_host_cordon_alert_min": 3,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RM_OPS_017_CONFIG_PATH", str(cfg_path))
    loaded = load_disk_full_cordon_config()
    assert loaded.trip.free_percent_below == 6.0
    assert loaded.multi_host_cordon_alert_min == 3


def test_parse_rejects_invalid_hysteresis() -> None:
    with pytest.raises(ValueError, match="readmit"):
        parse_disk_full_cordon_config(
            {
                "disk_full_cordon": {
                    "trip": {"free_percent_below": 10.0, "free_gb_below": 40.0},
                    "readmit": {"free_percent_above": 8.0, "free_gb_above": 60.0},
                }
            }
        )
