"""Boundary adapter for the locally vendored RM-CI-000 metric contract."""

from __future__ import annotations

from shared_scripts.ci_fleet_baseline.metrics import job_execute_seconds, job_queue_seconds


def job_durations(
    created_at: str | None,
    started_at: str | None,
    completed_at: str | None,
) -> tuple[float | None, float | None]:
    """Use canonical timing functions, treating malformed API timestamps as absent."""
    try:
        queue = job_queue_seconds(created_at, started_at)
    except (AttributeError, TypeError, ValueError):
        queue = None
    try:
        execute = job_execute_seconds(started_at, completed_at)
    except (AttributeError, TypeError, ValueError):
        execute = None
    return queue, execute
