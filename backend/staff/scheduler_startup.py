"""Hub startup grace for the staff scheduler (#3526).

Scheduled barb-family slots must not run slow GitHub/consolidation work during the
first seconds after process start, when ``/api/health`` must stay responsive for
systemd and ``node-self-heal.sh``.
"""

from __future__ import annotations

import os
import time

STARTUP_GRACE_ENV = "STAFF_SCHEDULER_STARTUP_GRACE_SECONDS"
DEFAULT_STARTUP_GRACE_SECONDS = 90.0
_BARB_FAMILY = frozenset({"barb", "rd-barb"})


def startup_grace_seconds() -> float:
    raw = os.environ.get(STARTUP_GRACE_ENV, "").strip()
    if not raw:
        return DEFAULT_STARTUP_GRACE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_STARTUP_GRACE_SECONDS
    return max(0.0, value)


def scheduler_started_at() -> float:
    """Monotonic time when the scheduler thread was first started; 0 before any start."""
    return _started_at


def mark_scheduler_started(now: float | None = None) -> None:
    global _started_at  # noqa: PLW0603
    if _started_at <= 0:
        _started_at = time.monotonic() if now is None else now


def in_startup_grace(now: float | None = None) -> bool:
    started = _started_at
    if started <= 0:
        return False
    when = time.monotonic() if now is None else now
    return (when - started) < startup_grace_seconds()


def skip_due_slot_during_startup(role_name: str, now: float | None = None) -> bool:
    """True when a due scheduled slot should wait for startup grace (barb-family only)."""
    return role_name in _BARB_FAMILY and in_startup_grace(now)


_started_at = 0.0
