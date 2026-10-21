"""Pure metric helpers for CI fleet baseline snapshots."""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from shared_scripts.ci_fleet_baseline.constants import FAILURE_DENOMINATOR_CONCLUSIONS
from shared_scripts.ci_fleet_baseline.window import parse_utc


def wall_clock_seconds(
    started_at: str | None, completed_at: str | None
) -> float | None:
    """Workflow-run wall clock: completed minus started (seconds)."""
    if not started_at or not completed_at:
        return None
    delta = parse_utc(completed_at) - parse_utc(started_at)
    return max(0.0, delta.total_seconds())


def job_queue_seconds(created_at: str | None, started_at: str | None) -> float | None:
    if not created_at or not started_at:
        return None
    delta = parse_utc(started_at) - parse_utc(created_at)
    return max(0.0, delta.total_seconds())


def job_execute_seconds(
    started_at: str | None, completed_at: str | None
) -> float | None:
    if not started_at or not completed_at:
        return None
    delta = parse_utc(completed_at) - parse_utc(started_at)
    return max(0.0, delta.total_seconds())


def time_of_day_bucket(iso_timestamp: str) -> str:
    """UTC hour bucket for stratified sampling."""
    hour = parse_utc(iso_timestamp).hour
    if 0 <= hour < 6:
        return "night"
    if 6 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    return "evening"


def failure_denominator_counts(runs: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Count conclusions for the failure-rate denominator stratum."""
    counts = {key: 0 for key in sorted(FAILURE_DENOMINATOR_CONCLUSIONS)}
    counts["skipped"] = 0
    counts["other"] = 0
    counts["retried_attempts"] = 0
    for run in runs:
        conclusion = str(run.get("conclusion") or "")
        attempt = int(run.get("run_attempt") or 1)
        if attempt > 1:
            counts["retried_attempts"] += 1
        if conclusion in FAILURE_DENOMINATOR_CONCLUSIONS:
            counts[conclusion] += 1
        elif conclusion == "skipped":
            counts["skipped"] += 1
        elif conclusion in ("", "in_progress", "neutral", "action_required"):
            continue
        else:
            counts["other"] += 1
    counts["denominator"] = sum(
        counts[key] for key in ("success", "failure", "cancelled", "timed_out")
    )
    return counts


def success_durations_by_event(
    runs: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Success-only wall-clock stats per workflow event."""
    by_event: dict[str, list[float]] = {}
    for run in runs:
        if run.get("conclusion") != "success":
            continue
        event = str(run.get("event") or "unknown")
        seconds = wall_clock_seconds(
            run.get("run_started_at") or run.get("started_at"),
            run.get("updated_at"),
        )
        if seconds is None:
            continue
        by_event.setdefault(event, []).append(seconds)
    result: dict[str, dict[str, Any]] = {}
    for event, values in sorted(by_event.items()):
        result[event] = duration_summary(values)
    return result


def duration_summary(values: Iterable[float]) -> dict[str, Any]:
    """Percentile summary for a list of durations in seconds."""
    sorted_vals = sorted(values)
    if not sorted_vals:
        return {"n": 0}
    n = len(sorted_vals)

    def pct(p: float) -> float:
        if n == 1:
            return sorted_vals[0]
        rank = (p / 100.0) * (n - 1)
        low = int(rank)
        high = min(low + 1, n - 1)
        weight = rank - low
        return sorted_vals[low] * (1 - weight) + sorted_vals[high] * weight

    return {
        "n": n,
        "p50_sec": round(pct(50), 3),
        "p90_sec": round(pct(90), 3),
        "mean_sec": round(statistics.fmean(sorted_vals), 3),
        "min_sec": round(sorted_vals[0], 3),
        "max_sec": round(sorted_vals[-1], 3),
    }


def armed_to_merged_seconds(
    auto_merge_at: str | None, merged_at: str | None
) -> float | None:
    if not auto_merge_at or not merged_at:
        return None
    delta = parse_utc(merged_at) - parse_utc(auto_merge_at)
    return max(0.0, delta.total_seconds())


def merge_timeline_event_in_window(
    event_time: str, window_start: str, window_end: str
) -> bool:
    when = parse_utc(event_time)
    return parse_utc(window_start) <= when <= parse_utc(window_end)
