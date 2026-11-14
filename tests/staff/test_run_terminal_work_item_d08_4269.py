"""A terminal run never leaves its work item ``in_progress`` (#4269, D-08).

Ruling (RM #2851, 2026-11-14): a failed or blocked run sets the linked work
item to ``blocked`` with the run id and the reason recorded; a cancelled run
returns it to ``open``; the item never stays ``in_progress`` once the run is
terminal. Only the item's most recent run may move it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from staff.run_link import update_linked_work_item
from staff.run_status import TERMINAL_STATUSES
from staff.store import RunRecord
from staff.work_items import (
    RUN_RELEASE_TRANSITIONS,
    VALID_TRANSITIONS,
    InvalidStateTransitionError,
    WorkItemStore,
    get_work_item_store,
    reset_work_item_store,
)

from staff import work_items as work_items_mod


@pytest.fixture
def wi_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[WorkItemStore]:
    db = tmp_path / "runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db))
    reset_work_item_store()
    yield get_work_item_store(db)
    reset_work_item_store()


@pytest.fixture
def audits(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(work_items_mod, "record_audit", lambda **kwargs: seen.append(kwargs))
    return seen


def _item_in(store: WorkItemStore, state: str) -> str:
    """Create a work item and walk it to ``state`` along legal transitions."""
    paths = {
        "open": [],
        "in_progress": ["in_progress"],
        "waiting_on_user": ["in_progress", "waiting_on_user"],
        "waiting_on_ci": ["in_progress", "waiting_on_ci"],
        "needs_evidence": ["in_progress", "needs_evidence"],
        "done": ["in_progress", "done"],
    }
    item = store.create_work_item(title="t", requested_by="op")
    for step in paths[state]:
        store.transition_state(item.id, step, actor="test", reason="setup")
    return item.id


def _run(work_item_id: str, run_id: str = "run-1", **extra: Any) -> RunRecord:
    return RunRecord(
        run_id, "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p", work_item_id=work_item_id, **extra
    )


def _state(store: WorkItemStore, work_item_id: str) -> str:
    item = store.get_work_item(work_item_id)
    assert item is not None
    return item.state


@pytest.mark.unit
@pytest.mark.parametrize("run_status", ["failed", "blocked"])
@pytest.mark.parametrize("before", ["open", "in_progress", "waiting_on_user"])
def test_a_failed_or_blocked_run_blocks_its_work_item(wi_store: WorkItemStore, run_status: str, before: str) -> None:
    item_id = _item_in(wi_store, before)

    update_linked_work_item(_run(item_id), run_status)

    assert _state(wi_store, item_id) == "blocked"


@pytest.mark.unit
def test_the_block_records_the_run_id_and_the_reason(wi_store: WorkItemStore, audits: list[dict[str, Any]]) -> None:
    item_id = _item_in(wi_store, "in_progress")
    run = _run(item_id, run_id="run-77", error="worker did not start", failure_class="bootstrap")

    update_linked_work_item(run, "failed")

    reason = audits[-1]["detail"]["reason"]
    assert audits[-1]["detail"]["target_state"] == "blocked"
    assert "run-77" in reason
    assert "bootstrap" in reason
    assert "worker did not start" in reason


@pytest.mark.unit
def test_a_long_error_is_cut_in_the_recorded_reason(wi_store: WorkItemStore, audits: list[dict[str, Any]]) -> None:
    item_id = _item_in(wi_store, "in_progress")

    update_linked_work_item(_run(item_id, error="x" * 5000), "failed")

    assert len(audits[-1]["detail"]["reason"]) <= 400


@pytest.mark.unit
@pytest.mark.parametrize("before", ["in_progress", "waiting_on_user"])
def test_a_cancelled_run_returns_its_work_item_to_open(wi_store: WorkItemStore, before: str) -> None:
    item_id = _item_in(wi_store, before)

    update_linked_work_item(_run(item_id), "cancelled")

    assert _state(wi_store, item_id) == "open"


@pytest.mark.unit
def test_a_run_cancelled_before_it_started_leaves_an_open_item_open(wi_store: WorkItemStore) -> None:
    item_id = _item_in(wi_store, "open")

    update_linked_work_item(_run(item_id), "cancelled")

    assert _state(wi_store, item_id) == "open"


@pytest.mark.unit
@pytest.mark.parametrize("run_status", sorted(s.value for s in TERMINAL_STATUSES))
def test_no_terminal_run_leaves_its_item_in_progress(wi_store: WorkItemStore, run_status: str) -> None:
    item_id = _item_in(wi_store, "in_progress")

    update_linked_work_item(_run(item_id), run_status)

    assert _state(wi_store, item_id) != "in_progress"


@pytest.mark.unit
@pytest.mark.parametrize("run_status", ["failed", "blocked", "cancelled"])
def test_an_older_run_does_not_move_an_item_a_newer_run_is_working(wi_store: WorkItemStore, run_status: str) -> None:
    item_id = _item_in(wi_store, "open")
    update_linked_work_item(_run(item_id, run_id="run-old"), "running")
    update_linked_work_item(_run(item_id, run_id="run-new"), "running")

    update_linked_work_item(_run(item_id, run_id="run-old"), run_status)

    assert _state(wi_store, item_id) == "in_progress"


@pytest.mark.unit
@pytest.mark.parametrize("run_status", ["failed", "blocked", "cancelled"])
@pytest.mark.parametrize("settled", ["waiting_on_ci", "needs_evidence", "done"])
def test_an_item_that_already_moved_on_is_left_alone(wi_store: WorkItemStore, run_status: str, settled: str) -> None:
    item_id = _item_in(wi_store, settled)

    update_linked_work_item(_run(item_id), run_status)

    assert _state(wi_store, item_id) == settled


@pytest.mark.unit
def test_a_retry_run_picks_up_an_item_an_earlier_run_left_blocked(wi_store: WorkItemStore) -> None:
    item_id = _item_in(wi_store, "in_progress")
    update_linked_work_item(_run(item_id, run_id="run-1"), "failed")
    assert _state(wi_store, item_id) == "blocked"

    update_linked_work_item(_run(item_id, run_id="run-2"), "running")
    assert _state(wi_store, item_id) == "in_progress"

    update_linked_work_item(_run(item_id, run_id="run-2", artifact="pr"), "succeeded")
    assert _state(wi_store, item_id) not in ("blocked", "in_progress")


@pytest.mark.unit
@pytest.mark.parametrize("before", ["in_progress", "waiting_on_user"])
def test_only_a_run_release_may_return_an_item_to_open(wi_store: WorkItemStore, before: str) -> None:
    """The operator-facing state machine still refuses the move back to ``open``."""
    item_id = _item_in(wi_store, before)

    with pytest.raises(InvalidStateTransitionError):
        wi_store.transition_state(item_id, "open", actor="op", reason="reopen")

    assert "open" not in VALID_TRANSITIONS[before]
    assert RUN_RELEASE_TRANSITIONS == {"in_progress": {"open"}, "waiting_on_user": {"open"}}
