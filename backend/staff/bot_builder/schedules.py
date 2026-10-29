"""Bounded schedules/triggers and declared-edge trigger cycle validator (RD #3107, slice 13)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from staff.bot_builder.schema import BuilderRoleDefinition, ScheduleBounds, TriggerEdge


@dataclass(frozen=True)
class SchedulePolicy:
    """Locked policy bounds for per-role schedules and concurrency."""

    max_runs_per_day: int = 24
    max_concurrency: int = 1
    quiet_hours_utc: tuple[int, int] = (0, 6)


DEFAULT_SCHEDULE_POLICY = SchedulePolicy()


class DeclaredEdgesCarrier(Protocol):
    """Minimal structural protocol reading ONLY role name and declared trigger edges (#3094 boundary)."""

    name: str
    triggers: tuple[TriggerEdge, ...]


def _quiet_hour_set(window: tuple[int, int]) -> frozenset[int]:
    start, end = window
    if start == end:
        return frozenset()
    if start < end:
        return frozenset(range(start, end))
    return frozenset(range(start, 24)) | frozenset(range(0, end))


def is_quiet_hour(hour_utc: int, quiet_hours_utc: tuple[int, int]) -> bool:
    """DbC: ``hour_utc`` in 0..23; returns True when inside ``quiet_hours_utc``."""
    if not isinstance(hour_utc, int) or not 0 <= hour_utc < 24:
        raise ValueError("hour_utc must be in 0..23")
    return hour_utc in _quiet_hour_set(quiet_hours_utc)


def can_run_at_hour(
    role: BuilderRoleDefinition,
    hour_utc: int,
    policy: SchedulePolicy = DEFAULT_SCHEDULE_POLICY,
) -> bool:
    """True when ``hour_utc`` is outside both policy and role quiet hours."""
    if is_quiet_hour(hour_utc, policy.quiet_hours_utc):
        return False
    if role.schedule_bounds is not None and is_quiet_hour(hour_utc, role.schedule_bounds.quiet_hours_utc):
        return False
    return True


def _cron_hours(schedule: str | None) -> tuple[int, ...]:
    if not schedule:
        return ()
    parts = schedule.strip().split()
    if len(parts) != 5:
        return ()
    hour_field = parts[1]
    if hour_field.isdigit():
        hour = int(hour_field)
        return (hour,) if 0 <= hour < 24 else ()
    return ()


def validate_schedule_bounds(
    role: BuilderRoleDefinition,
    policy: SchedulePolicy = DEFAULT_SCHEDULE_POLICY,
) -> tuple[str, ...]:
    """Validate role schedule and declared ScheduleBounds against locked policy."""
    reasons: list[str] = []
    policy_quiet = _quiet_hour_set(policy.quiet_hours_utc)

    if role.schedule_bounds is not None:
        bounds: ScheduleBounds = role.schedule_bounds
        if bounds.runs_per_day > policy.max_runs_per_day:
            reasons.append("schedule bounds exceed max_runs_per_day policy")
        if bounds.concurrency > policy.max_concurrency:
            reasons.append("schedule bounds exceed max_concurrency policy")
        role_quiet = _quiet_hour_set(bounds.quiet_hours_utc)
        if not policy_quiet.issubset(role_quiet):
            reasons.append("schedule bounds shrink locked quiet_hours policy")

    effective_quiet = (
        policy_quiet | _quiet_hour_set(role.schedule_bounds.quiet_hours_utc)
        if role.schedule_bounds is not None
        else policy_quiet
    )
    for hour in _cron_hours(role.schedule):
        if hour in effective_quiet:
            reasons.append("schedule fires during quiet_hours window")
            break

    return tuple(reasons)


def schedule_or_trigger_widening_reasons(
    previous: BuilderRoleDefinition,
    candidate: BuilderRoleDefinition,
) -> tuple[str, ...]:
    """Flag schedule-bounds or trigger-edge widening relative to ``previous``."""
    reasons: list[str] = []
    prev_b = previous.schedule_bounds
    cand_b = candidate.schedule_bounds
    if cand_b is not None:
        if prev_b is None:
            reasons.append("schedule_bounds")
        elif (
            cand_b.runs_per_day > prev_b.runs_per_day
            or cand_b.concurrency > prev_b.concurrency
            or not _quiet_hour_set(prev_b.quiet_hours_utc).issubset(_quiet_hour_set(cand_b.quiet_hours_utc))
        ):
            reasons.append("schedule_bounds")

    prev_edges = {(t.target_role, t.event) for t in previous.triggers}
    cand_edges = {(t.target_role, t.event) for t in candidate.triggers}
    if cand_edges - prev_edges:
        reasons.append("triggers")

    return tuple(reasons)


def extract_declared_edges(roles: Iterable[DeclaredEdgesCarrier]) -> dict[str, tuple[TriggerEdge, ...]]:
    """Project role carriers to declared trigger edges only (never reads content fields)."""
    return {role.name: tuple(role.triggers) for role in roles}


def find_trigger_cycle_reasons(
    role_name: str,
    candidate_triggers: Sequence[TriggerEdge],
    declared_edges_by_role: Mapping[str, Sequence[TriggerEdge]],
) -> tuple[str, ...]:
    """Detect any directed cycle reachable from ``role_name`` using declared edges only."""
    graph: dict[str, tuple[str, ...]] = {
        name: tuple(edge.target_role for edge in edges) for name, edges in declared_edges_by_role.items()
    }
    graph[role_name] = tuple(edge.target_role for edge in candidate_triggers)

    visiting: set[str] = set()
    visited: set[str] = set()

    def _dfs(node: str) -> bool:
        visiting.add(node)
        for nxt in graph.get(node, ()):
            if nxt in visiting:
                return True
            if nxt not in visited and _dfs(nxt):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    for start in tuple(graph):
        if start not in visited and _dfs(start):
            return ("cross-role trigger cycle detected in declared edges",)
    return ()
