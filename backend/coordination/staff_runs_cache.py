"""Last-good staff_runs snapshot for coordination degrade paths (#4458)."""

from __future__ import annotations

import threading
import time
from typing import Any

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


def snapshot_staff_runs() -> tuple[list[dict[str, Any]], list[str], bool]:
    """Return a copy of the last snapshot and whether any data was recorded."""
    with _lock:
        return list(_runs), list(_warnings), bool(_runs)