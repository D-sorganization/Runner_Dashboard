"""RunStatus StrEnum and compare-and-set transitions (#4269 PR 2)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from staff.run_status import (
    ACTIVE_STATUSES,
    LEGAL_TRANSITIONS,
    RUN_STATUSES,
    TERMINAL_STATUSES,
    IllegalRunStatusTransitionError,
    RunStatus,
    RunStatusRaceError,
    is_legal_transition,
    transition,
)
from staff.store import RunRecord, RunStore

from tests.staff.test_run_status_characterization_4269 import PINNED_ACTIVE_STATUSES, PINNED_RUN_STATUSES


@pytest.mark.unit
def test_run_status_enum_values_match_legacy_literals() -> None:
    assert tuple(RunStatus) == PINNED_RUN_STATUSES
    assert RUN_STATUSES == PINNED_RUN_STATUSES
    assert ACTIVE_STATUSES == PINNED_ACTIVE_STATUSES


@pytest.mark.unit
def test_transition_table_is_closed_and_terminal_states_sticky() -> None:
    for status in RunStatus:
        assert status in LEGAL_TRANSITIONS
        if status in TERMINAL_STATUSES:
            assert LEGAL_TRANSITIONS[status] == frozenset(), status
    assert not is_legal_transition(RunStatus.CANCELLED, RunStatus.RUNNING)
    assert not is_legal_transition(RunStatus.BLOCKED, RunStatus.RUNNING)
    assert not is_legal_transition(RunStatus.FAILED, RunStatus.SUCCEEDED)
    assert not is_legal_transition(RunStatus.SUCCEEDED, RunStatus.FAILED)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        (RunStatus.QUEUED, RunStatus.PREPARING),
        (RunStatus.PREPARING, RunStatus.RUNNING),
        (RunStatus.RUNNING, RunStatus.SUCCEEDED),
        (RunStatus.RUNNING, RunStatus.NEEDS_INPUT),
    ],
)
def test_legal_transitions_succeed(from_status: RunStatus, to_status: RunStatus) -> None:
    assert is_legal_transition(from_status, to_status)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        (RunStatus.FAILED, RunStatus.SUCCEEDED),
        (RunStatus.SUCCEEDED, RunStatus.FAILED),
        (RunStatus.CANCELLED, RunStatus.FAILED),
        (RunStatus.CANCELLED, RunStatus.RUNNING),
    ],
)
def test_terminal_lifecycle_states_reject_outbound_transitions(
    from_status: RunStatus, to_status: RunStatus
) -> None:
    assert not is_legal_transition(from_status, to_status)


@pytest.mark.unit
def test_illegal_transition_raises(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "ill.sqlite3")
    store.create_run(RunRecord("ill-1", "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p", status="cancelled"))
    try:
        with pytest.raises(IllegalRunStatusTransitionError):
            transition(store, "ill-1", RunStatus.CANCELLED, RunStatus.RUNNING)
    finally:
        store.close()


@pytest.mark.unit
def test_compare_and_set_race(tmp_path: Path) -> None:
    db = tmp_path / "runs.sqlite3"
    store = RunStore(db)
    store.create_run(RunRecord("cas-1", "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p", status="queued"))
    try:
        transition(store, "cas-1", RunStatus.QUEUED, RunStatus.PREPARING)
        with pytest.raises(RunStatusRaceError):
            transition(store, "cas-1", RunStatus.QUEUED, RunStatus.CANCELLED)
        got = store.get_run("cas-1")
        assert got is not None and got.status == "preparing"
    finally:
        store.close()


@pytest.mark.unit
def test_compare_and_set_concurrent_single_winner(tmp_path: Path) -> None:
    db = tmp_path / "race.sqlite3"
    store = RunStore(db)
    store.create_run(RunRecord("race-1", "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p", status="queued"))
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def racer(target: RunStatus) -> None:
        barrier.wait(5)
        try:
            transition(store, "race-1", RunStatus.QUEUED, target, started_at="2026-11-12T00:00:00Z")
            outcomes.append("ok")
        except RunStatusRaceError:
            outcomes.append("lost")

    threads = [
        threading.Thread(target=racer, args=(RunStatus.PREPARING,)),
        threading.Thread(target=racer, args=(RunStatus.CANCELLED,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert outcomes.count("ok") == 1
    assert outcomes.count("lost") == 1
    final = store.get_run("race-1")
    assert final is not None
    assert final.status in {"preparing", "cancelled"}
    store.close()


@pytest.mark.unit
def test_raw_update_run_still_allows_cancelled_to_running(tmp_path: Path) -> None:
    """Characterization: unconditional ``update_run`` remains for non-transition callers."""
    store = RunStore(tmp_path / "raw.sqlite3")
    try:
        store.create_run(RunRecord("reopen", "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p", status="queued"))
        store.update_run("reopen", status="cancelled", ended_at="2026-11-11T00:00:00Z")
        store.update_run("reopen", status="running")
        got = store.get_run("reopen")
        assert got is not None and got.status == "running"
    finally:
        store.close()
