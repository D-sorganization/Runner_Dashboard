"""Slice 1 contract tests for RunStatus StrEnum + transition() (#4269)."""

from __future__ import annotations

from pathlib import Path

import pytest
from staff.run_status import (
    IllegalRunStatusTransitionError,
    RunStatus,
    is_legal_transition,
    transition,
)
from staff.run_status_contract import validate_run_status_contract
from staff.store import RunRecord, RunStore

from tests.staff.test_run_status_characterization_4269 import PINNED_RUN_STATUSES


def test_contract_validates_at_import() -> None:
    validate_run_status_contract()


def test_run_status_str_enum_matches_pinned_literals() -> None:
    assert [s.value for s in RunStatus] == list(PINNED_RUN_STATUSES)


def test_terminal_states_have_no_outbound_edges() -> None:
    for terminal in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.BLOCKED):
        assert not is_legal_transition(terminal, RunStatus.RUNNING)
        assert not is_legal_transition(terminal, RunStatus.QUEUED)


def test_transition_compare_and_set_is_only_writer_for_lifecycle_move(tmp_path: Path) -> None:
    """Raw update_run still patches non-status fields; lifecycle moves use transition()."""
    store = RunStore(tmp_path / "slice1.sqlite3")
    try:
        store.create_run(RunRecord("s1", "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p", status="queued"))
        transition(store, "s1", RunStatus.QUEUED, RunStatus.PREPARING)
        store.update_run("s1", failure_class="booting")
        got = store.get_run("s1")
        assert got is not None and got.status == "preparing" and got.failure_class == "booting"
        with pytest.raises(IllegalRunStatusTransitionError):
            transition(store, "s1", RunStatus.QUEUED, RunStatus.RUNNING)
    finally:
        store.close()
