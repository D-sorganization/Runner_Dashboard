"""RD-OPS-017 disk-full cordon (issue #2030)."""

from __future__ import annotations

from dataclasses import replace

from disk_full_cordon import (
    CordonActionKind,
    DiskFullCordonState,
    RunnerSnapshot,
    disk_pressure_allows_readmit,
    disk_pressure_trips,
    multi_host_cordon_alert_needed,
    plan_cordon_tick,
)
from shared_scripts.runner_disk_guard_config import (
    DiskGuardAlerts,
    DiskGuardCleanupCommand,
    DiskGuardCordon,
    DiskGuardDrain,
    DiskGuardThresholds,
    RunnerDiskGuardConfig,
)


def _cfg(**overrides: object) -> RunnerDiskGuardConfig:
    base = RunnerDiskGuardConfig(
        version=1,
        schema="runner-disk-guard/v1",
        updated_at="2026-10-21",
        thresholds=DiskGuardThresholds(
            trip_min_free_gb=25.0,
            readmit_min_free_gb=40.0,
            trip_used_percent=92.0,
            readmit_used_percent=85.0,
        ),
        drain=DiskGuardDrain(timeout_seconds=120, finish_running_jobs=True),
        cordon=DiskGuardCordon(min_online_after_drain=0, stop_accepting_jobs_on_trip=True),
        cleanup=(
            DiskGuardCleanupCommand(
                argv=("/usr/local/bin/runner-cleanup", "--disk-guard"),
                description="disk-guard",
            ),
        ),
        alerts=DiskGuardAlerts(cordoned_hosts_warning_threshold=2),
    )
    if not overrides:
        return base
    return replace(
        base,
        thresholds=overrides.get("thresholds", base.thresholds),
        drain=overrides.get("drain", base.drain),
        cordon=overrides.get("cordon", base.cordon),
        cleanup=overrides.get("cleanup", base.cleanup),
        alerts=overrides.get("alerts", base.alerts),
    )


def test_disk_pressure_trips_on_used_percent_or_low_free_gb() -> None:
    policy = _cfg()
    thresholds = policy.thresholds
    assert disk_pressure_trips(20.0, 93.0, thresholds) is True
    assert disk_pressure_trips(24.0, 50.0, thresholds) is True
    assert disk_pressure_trips(30.0, 90.0, thresholds) is False


def test_readmit_uses_hysteresis_or_semantics() -> None:
    policy = _cfg()
    thresholds = policy.thresholds
    assert disk_pressure_allows_readmit(45.0, 90.0, thresholds) is True
    assert disk_pressure_allows_readmit(30.0, 80.0, thresholds) is True
    assert disk_pressure_allows_readmit(30.0, 90.0, thresholds) is False


def test_trip_starts_drain_and_sets_min_online_zero() -> None:
    state = DiskFullCordonState()
    runners = [
        RunnerSnapshot("u1", active=True, busy=True),
        RunnerSnapshot("u2", active=True, busy=False),
    ]
    result = plan_cordon_tick(
        state,
        free_gb=20.0,
        used_percent=93.0,
        config=_cfg(),
        runners=runners,
        now=1000.0,
    )
    assert result.state.phase == "tripping"
    assert result.effective_min_online == 0
    stops = [a for a in result.actions if a.kind == CordonActionKind.STOP_RUNNER]
    assert len(stops) == 1
    assert stops[0].unit == "u2"


def test_drain_timeout_force_stops_busy_runners() -> None:
    state = DiskFullCordonState(phase="tripping", tripped_at=1000.0)
    runners = [RunnerSnapshot("u1", active=True, busy=True)]
    result = plan_cordon_tick(
        state,
        free_gb=20.0,
        used_percent=93.0,
        config=_cfg(),
        runners=runners,
        now=1125.0,
    )
    stop = next(a for a in result.actions if a.kind == CordonActionKind.STOP_RUNNER)
    assert stop.force is True


def test_finish_running_jobs_false_stops_busy_immediately() -> None:
    state = DiskFullCordonState(phase="tripping", tripped_at=1000.0)
    runners = [RunnerSnapshot("u1", active=True, busy=True)]
    drain = DiskGuardDrain(finish_running_jobs=False, timeout_seconds=7200)
    result = plan_cordon_tick(
        state,
        free_gb=20.0,
        used_percent=93.0,
        config=_cfg(drain=drain),
        runners=runners,
        now=1001.0,
    )
    stop = next(a for a in result.actions if a.kind == CordonActionKind.STOP_RUNNER)
    assert stop.force is True


def test_cordoned_runs_cleanup_once_then_readmits_with_hysteresis() -> None:
    state = DiskFullCordonState(phase="cordoned", tripped_at=900.0, cleanup_done=False)
    policy = _cfg()
    low = plan_cordon_tick(
        state,
        free_gb=55.0,
        used_percent=50.0,
        config=policy,
        runners=[],
        now=2000.0,
    )
    assert any(a.kind == CordonActionKind.RUN_DISK_CLEANUP for a in low.actions)
    assert low.state.phase == "cordoned"

    cleared = plan_cordon_tick(
        low.state,
        free_gb=55.0,
        used_percent=50.0,
        config=policy,
        runners=[],
        now=2001.0,
    )
    assert cleared.state.phase == "normal"


def test_multi_host_cordon_alert_fires_at_threshold() -> None:
    assert multi_host_cordon_alert_needed(None, 2, 2) is True
    assert multi_host_cordon_alert_needed(1, 2, 2) is True
    assert multi_host_cordon_alert_needed(2, 2, 2) is False
