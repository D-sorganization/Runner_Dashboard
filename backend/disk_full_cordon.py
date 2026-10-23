"""Disk-full host cordon state machine (RD-OPS-017 / issue #2030).

Thresholds and drain policy come from :class:`RunnerDiskGuardConfig` (RM
``runner-disk-guard/v1``); not redefined here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from runner_disk_guard_policy import DiskGuardThresholds, RunnerDiskGuardConfig

CordonPhase = Literal["normal", "tripping", "cordoned"]


class CordonActionKind(str, Enum):
    SET_MAINTENANCE_ON = "set_maintenance_on"
    SET_MAINTENANCE_OFF = "set_maintenance_off"
    STOP_RUNNER = "stop_runner"
    RUN_DISK_CLEANUP = "run_disk_cleanup"


@dataclass(frozen=True, slots=True)
class CordonAction:
    kind: CordonActionKind
    unit: str | None = None
    force: bool = False


@dataclass(slots=True)
class RunnerSnapshot:
    unit: str
    active: bool
    busy: bool


PROACTIVE_CLEANUP_USED_PERCENT: float = 75.0
PROACTIVE_CLEANUP_MIN_FREE_GB: float = 80.0
PROACTIVE_CLEANUP_COOLDOWN_S: float = 3600.0


@dataclass(slots=True)
class DiskFullCordonState:
    phase: CordonPhase = "normal"
    tripped_at: float | None = None
    cleanup_done: bool = False
    last_proactive_cleanup_at: float | None = None


@dataclass(frozen=True, slots=True)
class CordonTickResult:
    state: DiskFullCordonState
    actions: tuple[CordonAction, ...]
    scheduling_inhibited: bool
    effective_min_online: int | None
    cordon_active: bool


def proactive_cleanup_needed(free_gb: float, used_percent: float) -> bool:
    """True when host volume reaches soft pre-cordon cleanup thresholds."""
    return used_percent >= PROACTIVE_CLEANUP_USED_PERCENT or free_gb < PROACTIVE_CLEANUP_MIN_FREE_GB


def disk_pressure_trips(free_gb: float, used_percent: float, thresholds: DiskGuardThresholds) -> bool:
    """True when host volume trips RM thresholds (free GB **or** used %)."""
    return free_gb < thresholds.trip_min_free_gb or used_percent >= thresholds.trip_used_percent


def disk_pressure_allows_readmit(free_gb: float, used_percent: float, thresholds: DiskGuardThresholds) -> bool:
    """True when host volume cleared hysteresis (free GB **or** used %)."""
    return free_gb >= thresholds.readmit_min_free_gb or used_percent <= thresholds.readmit_used_percent


def _active_runners(runners: list[RunnerSnapshot]) -> list[RunnerSnapshot]:
    return [r for r in runners if r.active]


def plan_cordon_tick(
    state: DiskFullCordonState,
    *,
    free_gb: float,
    used_percent: float,
    config: RunnerDiskGuardConfig,
    runners: list[RunnerSnapshot],
    now: float,
) -> CordonTickResult:
    """Advance cordon state for one autoscaler tick. Pure aside from *state* copy."""
    thresholds = config.thresholds
    actions: list[CordonAction] = []
    next_state = DiskFullCordonState(
        phase=state.phase,
        tripped_at=state.tripped_at,
        cleanup_done=state.cleanup_done,
        last_proactive_cleanup_at=state.last_proactive_cleanup_at,
    )

    if state.phase == "normal":
        if disk_pressure_trips(free_gb, used_percent, thresholds):
            next_state.phase = "tripping"
            next_state.tripped_at = now
            actions.append(CordonAction(CordonActionKind.SET_MAINTENANCE_ON))
        else:
            if proactive_cleanup_needed(free_gb, used_percent) and (
                state.last_proactive_cleanup_at is None
                or (now - state.last_proactive_cleanup_at) >= PROACTIVE_CLEANUP_COOLDOWN_S
            ):
                next_state.last_proactive_cleanup_at = now
                actions.append(CordonAction(CordonActionKind.RUN_DISK_CLEANUP))
            return _finish_tick(next_state, actions, config)

    if next_state.phase == "tripping":
        assert next_state.tripped_at is not None  # noqa: S101
        drain_elapsed = now - next_state.tripped_at
        force_stop = not config.drain.finish_running_jobs or drain_elapsed >= config.drain.timeout_seconds
        for runner in _active_runners(runners):
            if runner.busy and not force_stop:
                continue
            actions.append(
                CordonAction(CordonActionKind.STOP_RUNNER, unit=runner.unit, force=force_stop and runner.busy)
            )
        if not _active_runners(runners):
            next_state.phase = "cordoned"
        return _finish_tick(next_state, actions, config)

    if next_state.phase == "cordoned":
        if not state.cleanup_done:
            actions.append(CordonAction(CordonActionKind.RUN_DISK_CLEANUP))
            next_state.cleanup_done = True
            return _finish_tick(next_state, actions, config)
        if disk_pressure_allows_readmit(free_gb, used_percent, thresholds):
            next_state = DiskFullCordonState(phase="normal")
            actions.append(CordonAction(CordonActionKind.SET_MAINTENANCE_OFF))
        return _finish_tick(next_state, actions, config)

    return _finish_tick(next_state, actions, config)


def _finish_tick(
    state: DiskFullCordonState,
    actions: list[CordonAction],
    config: RunnerDiskGuardConfig,
) -> CordonTickResult:
    cordon_active = state.phase != "normal"
    scheduling_inhibited = cordon_active
    effective_min_online: int | None = config.cordon.min_online_after_drain if cordon_active else None
    deduped: list[CordonAction] = []
    seen_stops: set[str] = set()
    for action in actions:
        if action.kind == CordonActionKind.STOP_RUNNER and action.unit:
            if action.unit in seen_stops:
                continue
            seen_stops.add(action.unit)
        deduped.append(action)
    return CordonTickResult(
        state=state,
        actions=tuple(deduped),
        scheduling_inhibited=scheduling_inhibited,
        effective_min_online=effective_min_online,
        cordon_active=cordon_active,
    )


def multi_host_cordon_alert_needed(
    previous_count: int | None,
    current_count: int,
    minimum: int,
) -> bool:
    """Emit when ``current_count`` reaches ``minimum`` from below."""
    if current_count < minimum:
        return False
    if previous_count is None:
        return True
    return previous_count < minimum
