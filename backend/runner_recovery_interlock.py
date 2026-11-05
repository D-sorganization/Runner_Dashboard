"""Shared stop/defer decision for runner recovery paths (#4046 slice 1).

CT-NVMe and other recovery scripts must not stop a runner between an idle
check and systemd stop when a job-started hook lock shows a live Worker.
Uses the same staleness rule as ``runner_busy_lock`` and ``runner-scheduler``.
"""

from __future__ import annotations

from pathlib import Path

from runner_busy_lock import lock_is_stale_by_worker_pid, worker_pid


def should_defer_runner_stop(lock_text: str, *, proc_root: Path | None = None) -> bool:
    """Return True when a stop must be deferred because a job is in flight.

    A lock without ``worker_pid`` is treated as busy (legacy hooks): defer until
    age-based cleanup clears it. A lock with a live ``worker_pid`` defers; only
    stale locks allow stop.
    """
    if not lock_text.strip():
        return False
    if worker_pid(lock_text) is None:
        return True
    return not lock_is_stale_by_worker_pid(lock_text, proc_root)
