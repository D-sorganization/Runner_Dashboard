"""Last-good staff_runs snapshot for coordination degrade paths (#4458)."""

from __future__ import annotations

import threading
import time
from typing import Any

STAFF_RUNS_SNAPSHOT_MAX_AGE_S = 60.0

_lock = threading.RLock()
_runs: list[dict[str, Any]] = []
_warnings: list[str] = []
_recorded_at: float = 0.0


def reset_staff_runs_cache() -> None:
    """Test helper."""
    global _recorded_at
    with _lock:
        _runs.clear()
        _warnings.clear()
        _recorded_at = 0.0


def record_staff_runs_snapshot(runs: list[dict[str, Any]], warnings: list[str]) -> None:
    """Persist the latest successful fleet staff-runs read."""
    global _recorded_at
    with _lock:
        _runs.clear()
        _runs.extend(runs)
        _warnings.clear()
        _warnings.extend(warnings)
        _recorded_at = time.monotonic()


def snapshot_staff_runs(
    max_age_s: float | None = None,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    """Return a copy of the last snapshot when younger than ``max_age_s`` (default bound)."""
    bound = STAFF_RUNS_SNAPSHOT_MAX_AGE_S if max_age_s is None else max_age_s
    with _lock:
        if not _runs or not _recorded_at:
            return [], [], False
        if time.monotonic() - _recorded_at > bound:
            return [], [], False
        return list(_runs), list(_warnings), True
