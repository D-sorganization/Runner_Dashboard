"""Process-wide StaffRunner singleton (split from ``staff.runner``, 500-line cap)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from staff.runner import StaffRunner

_runner: StaffRunner | None = None
_runner_lock = threading.Lock()


def get_runner() -> StaffRunner:
    from staff.runner import StaffRunner

    global _runner  # noqa: PLW0603
    with _runner_lock:
        if _runner is None:
            _runner = StaffRunner()
        return _runner


def reset_runner() -> None:
    """Drop the singleton, waiting for its outstanding worker threads first (#3778).

    Nulling the reference without joining leaves any in-flight daemon thread
    racing the next test's store/global-state reset.
    """
    global _runner  # noqa: PLW0603
    with _runner_lock:
        runner, _runner = _runner, None
    if runner is not None:
        runner.join_all()
