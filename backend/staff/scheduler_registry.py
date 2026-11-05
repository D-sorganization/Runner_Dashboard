"""Process-wide StaffScheduler singleton (split from ``staff.scheduler``, 500-line cap)."""

from __future__ import annotations

import sys
import threading
from typing import TYPE_CHECKING

from staff.budget import BudgetGuard
from staff.holds import HoldsList
from staff.runner import get_runner

if TYPE_CHECKING:
    from staff.scheduler import StaffScheduler

_scheduler: StaffScheduler | None = None
_scheduler_lock = threading.Lock()
_built_scheduler: StaffScheduler | None = None  # the instance get_scheduler() constructed (#3622)


def _injected_scheduler() -> StaffScheduler | None:
    """Tests patch ``staff.scheduler._scheduler``; honor that before the singleton."""
    sched_mod = sys.modules.get("staff.scheduler")
    if sched_mod is None:
        return None
    return getattr(sched_mod, "_scheduler", None)


def get_scheduler() -> StaffScheduler:
    """Process-wide scheduler bound to the process-wide runner.

    A scheduler built here follows the runner: once the process-wide runner (and its
    store) is replaced it is rebuilt instead of keeping a budget guard on a closed store
    (#3622). An injected ``_scheduler`` is returned as is.
    """
    from staff.scheduler import StaffScheduler

    injected = _injected_scheduler()
    if injected is not None:
        return injected

    global _scheduler, _built_scheduler  # noqa: PLW0603
    with _scheduler_lock:
        runner = get_runner()
        was_running = False
        if _scheduler is not None and _scheduler is _built_scheduler and _scheduler.runner is not runner:
            was_running = _scheduler.running
            _scheduler.stop()
            _scheduler = None
        if _scheduler is None:
            _scheduler = StaffScheduler(runner, HoldsList(roles_loader=runner.roles), BudgetGuard(runner.store))
            _built_scheduler = _scheduler
            if was_running:
                _scheduler.start()
        return _scheduler


def reset_scheduler() -> None:
    sched_mod = sys.modules.get("staff.scheduler")
    if sched_mod is not None:
        vars(sched_mod)["_scheduler"] = None
    global _scheduler  # noqa: PLW0603
    with _scheduler_lock:
        if _scheduler is not None:
            _scheduler.stop()
        _scheduler = None
