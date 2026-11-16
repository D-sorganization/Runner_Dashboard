"""Terminal run rows stay sticky: raw ``update_run`` cannot reopen them (#4269, D-05).

PR 2 routed product writes through ``transition()`` with compare-and-set, but
``RunStore.update_run`` still allowed a cancelled row to flip back to
``running``. That bypassed the state machine and skipped the notifier.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from staff.run_status import TERMINAL_STATUSES, IllegalRunStatusTransitionError, RunStatus
from staff.store import RunRecord, RunStore


@pytest.mark.unit
@pytest.mark.parametrize("terminal", sorted(s.value for s in TERMINAL_STATUSES))
@pytest.mark.parametrize("target", ["running", "queued", "preparing", "needs_input"])
def test_update_run_rejects_reopening_a_terminal_row(tmp_path: Path, terminal: str, target: str) -> None:
    store = RunStore(tmp_path / "sticky.sqlite3")
    try:
        store.create_run(RunRecord("reopen", "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p", status=terminal))
        with pytest.raises(IllegalRunStatusTransitionError):
            store.update_run("reopen", status=target)
        got = store.get_run("reopen")
        assert got is not None and got.status == terminal
    finally:
        store.close()


@pytest.mark.unit
@pytest.mark.parametrize("terminal", sorted(s.value for s in TERMINAL_STATUSES))
def test_update_run_allows_non_status_patch_on_terminal_rows(tmp_path: Path, terminal: str) -> None:
    store = RunStore(tmp_path / f"patch-{terminal}.sqlite3")
    try:
        store.create_run(RunRecord("t1", "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p", status=terminal))
        store.update_run("t1", status=terminal, cost_usd=2.5)
        got = store.get_run("t1")
        assert got is not None
        assert got.status == terminal
        assert got.cost_usd == 2.5
    finally:
        store.close()


@pytest.mark.unit
def test_update_run_rejects_terminal_to_terminal_rewrite(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "term-term.sqlite3")
    try:
        store.create_run(RunRecord("x", "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p", status="cancelled"))
        with pytest.raises(IllegalRunStatusTransitionError):
            store.update_run("x", status=RunStatus.FAILED.value)
    finally:
        store.close()


@pytest.mark.unit
def test_status_update_cannot_overwrite_cancellation_after_lock_release(tmp_path: Path) -> None:
    """Schedule cancellation at the first unlock, exposing a split read/write lock."""
    from contextlib import contextmanager

    store = RunStore(tmp_path / "race.sqlite3")
    store.create_run(RunRecord("race", "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p", status="queued"))
    lock = store._lock
    cancelled = False

    @contextmanager
    def cancel_on_first_unlock():
        nonlocal cancelled
        with lock:
            yield
        if not cancelled:
            cancelled = True
            current = store.get_run("race")
            assert current is not None
            assert store.compare_and_set_run_status("race", expected=current.status, status="cancelled")

    class ScheduledLock:
        def __enter__(self):
            self.context = cancel_on_first_unlock()
            return self.context.__enter__()

        def __exit__(self, *args):
            return self.context.__exit__(*args)

    store._lock = ScheduledLock()
    try:
        store.update_run("race", status="running")
        got = store.get_run("race")
        assert got is not None and got.status == "cancelled"
    finally:
        store._lock = lock
        store.close()
