"""Disk-full host cordon state machine (RD-OPS-017 / issue #2030).

Pure decision logic: trip thresholds, hysteresis readmit, bounded drain, then
take all runners offline (``MIN_ONLINE=0``). Side effects are applied by
``disk_full_cordon_runtime``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from rm_ops_017_config import DiskFullCordonConfig, DiskFullCordonReadmitThresholds, DiskFullCordonTripThresholds

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


@dataclass(slots=True)
class DiskFullCordonState:
    phase: CordonPhase = "normal"
    tripped_at: float | None = None
    cleanup_done: bool = False


@dataclass(frozen=True, slots=True)
class CordonTickResult:
    state: DiskFullCordonState
    actions: tuple[CordonAction, ...]
    scheduling_inhibited: bool
    effective_min_online: int | None
    cordon_active: bool


def disk_pressure_trips(
    free_gb: float,
    free_percent: float,
    trip: DiskFullCordonTripThresholds,
) -> bool:
    """True when host volume is below the trip floor (either metric)."""
    return free_percent < trip.free_percent_below or free_gb < trip.free_gb_below


def disk_pressure_allows_readmit(
    free_gb: float,
    free_percent: float,
    readmit: DiskFullCordonReadmitThresholds,
) -> bool:
    """True when host volume cleared the higher readmit bar (both metrics)."""
    return free_percent >= readmit.free_percent_above and free_gb >= readmit.free_gb_above


def _active_runners(runners: list[RunnerSnapshot]) -> list[RunnerSnapshot]:
    return [r for r in runners if r.active]


def plan_cordon_tick(
    state: DiskFullCordonState,
    *,
    free_gb: float,
    free_percent: float,
    config: DiskFullCordonConfig,
    runners: list[RunnerSnapshot],
    now: float,
) -> CordonTickResult:
    """Advance cordon state for one autoscaler tick. Pure aside from *state* copy."""
    if not config.enabled:
        cleared = DiskFullCordonState(phase="normal")
        return CordonTickResult(
            state=cleared,
            actions=(),
            scheduling_inhibited=False,
            effective_min_online=None,
            cordon_active=False,
        )

    actions: list[CordonAction] = []
    next_state = DiskFullCordonState(
        phase=state.phase,
        tripped_at=state.tripped_at,
        cleanup_done=state.cleanup_done,
    )

    if state.phase == "normal":
        if disk_pressure_trips(free_gb, free_percent, config.trip):
            next_state.phase = "tripping"
            next_state.tripped_at = now
            actions.append(CordonAction(CordonActionKind.SET_MAINTENANCE_ON))
        else:
            return _finish_tick(next_state, actions)

    if next_state.phase == "tripping":
        assert next_state.tripped_at is not None  # noqa: S101
        force_stop = (now - next_state.tripped_at) >= config.drain_timeout_seconds
        for runner in _active_runners(runners):
            if runner.busy and not force_stop:
                continue
            actions.append(
                CordonAction(CordonActionKind.STOP_RUNNER, unit=runner.unit, force=force_stop and runner.busy)
            )
        if not _active_runners(runners):
            next_state.phase = "cordoned"
        return _finish_tick(next_state, actions)

    if next_state.phase == "cordoned":
        if not state.cleanup_done:
            actions.append(CordonAction(CordonActionKind.RUN_DISK_CLEANUP))
            next_state.cleanup_done = True
            return _finish_tick(next_state, actions)
        if disk_pressure_allows_readmit(free_gb, free_percent, config.readmit):
            next_state = DiskFullCordonState(phase="normal")
            actions.append(CordonAction(CordonActionKind.SET_MAINTENANCE_OFF))
        return _finish_tick(next_state, actions)

    return _finish_tick(next_state, actions)


def _finish_tick(
    state: DiskFullCordonState,
    actions: list[CordonAction],
) -> CordonTickResult:
    cordon_active = state.phase != "normal"
    scheduling_inhibited = cordon_active
    effective_min_online: int | None = 0 if cordon_active else None
    # Deduplicate stop actions for the same unit (tripping may append twice on timeout edge).
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


def count_cordoned_hosts(nodes: list[tuple[str, bool]]) -> int:
    """Count fleet nodes reporting an active disk-full cordon."""
    return sum(1 for _name, active in nodes if active)


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
