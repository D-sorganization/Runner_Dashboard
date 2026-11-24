"""Stacked slice 3: transition() → terminal notifier → work-item settlement (#4269).

PR 3 landed on main in #4456 (D-06) and #4470 (D-08). This file pins the full
pipeline on top of the slice-1 contract branch: a compare-and-set terminal write
must call ``notify_run_terminal`` exactly once and move linked work items off
``in_progress`` (or leave non-held states untouched).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from staff.run_status import RunStatus, transition, transition_to
from staff.store import RunRecord, RunStore
from staff.work_items import WorkItemStore, get_work_item_store, reset_work_item_store

from staff import run_terminal

_PIPELINE_SOURCES = (
    ("queued", RunStatus.FAILED),
    ("running", RunStatus.FAILED),
    ("running", RunStatus.CANCELLED),
    ("running", RunStatus.BLOCKED),
    ("needs_input", RunStatus.CANCELLED),
    ("preparing", RunStatus.FAILED),
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RunStore]:
    run_store = RunStore(tmp_path / "runs.sqlite3")
    yield run_store
    run_store.close()


@pytest.fixture
def wi_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[WorkItemStore]:
    db = tmp_path / "runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db))
    reset_work_item_store()
    yield get_work_item_store(db)
    reset_work_item_store()


@pytest.fixture
def notifier_safe_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let ``handle_run_status_change`` run work-item + session cleanup without I/O."""

    monkeypatch.setattr("dispatch_queue_link.touch_run_session", lambda _run_id: None)
    monkeypatch.setattr("dispatch_queue_link.release_run_session", lambda _run_id, **_: None)
    monkeypatch.setattr("staff.claim_guard.release_claim", lambda *_args, **_kwargs: None)

    class _Cfg:
        is_replica = False
        is_authority = True
        epoch = 1
        node = "test-node"

    monkeypatch.setattr("staff.run_link.get_authority_config", lambda: _Cfg())
    monkeypatch.setattr("staff.run_link.post_run_card", lambda *_, **__: None)
    monkeypatch.setattr("staff.run_link.relay_run_card_to_origin", lambda *_, **__: False)
    monkeypatch.setattr("staff.run_link.threading.Thread", MagicMock())


@pytest.fixture
def notify_spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    real = run_terminal.notify_run_terminal

    def _spy(st: RunStore, run_id: str, status: RunStatus | str) -> None:
        calls.append((run_id, str(getattr(status, "value", status))))
        real(st, run_id, status)

    monkeypatch.setattr(run_terminal, "notify_run_terminal", _spy)
    return calls


def _work_item_in_progress(wi_store: WorkItemStore) -> str:
    item = wi_store.create_work_item(title="slice3", requested_by="op")
    wi_store.transition_state(item.id, "in_progress", actor="test", reason="setup")
    return item.id


def _seed_run(store: RunStore, run_id: str, source: str, work_item_id: str, **extra: Any) -> None:
    store.create_run(
        RunRecord(
            run_id,
            "ad-hoc",
            "claude",
            None,
            "m",
            "repo",
            "prompt",
            "",
            "p",
            status=source,
            work_item_id=work_item_id,
            **extra,
        )
    )


@pytest.mark.unit
@pytest.mark.parametrize(("source", "target"), _PIPELINE_SOURCES)
def test_each_legal_terminal_move_notifies_once_and_settles_work_item(
    store: RunStore,
    wi_store: WorkItemStore,
    notifier_safe_path: None,
    notify_spy: list[tuple[str, str]],
    source: str,
    target: RunStatus,
) -> None:
    item_id = _work_item_in_progress(wi_store)
    _seed_run(
        store,
        "pipe-1",
        source,
        item_id,
        error="worker boom" if target in (RunStatus.FAILED, RunStatus.BLOCKED) else "",
        failure_class="test" if target in (RunStatus.FAILED, RunStatus.BLOCKED) else "",
    )

    transition(store, "pipe-1", source, target)

    assert notify_spy == [("pipe-1", target.value)]
    item = wi_store.get_work_item(item_id)
    assert item is not None
    if target in (RunStatus.FAILED, RunStatus.BLOCKED):
        assert item.state == "blocked"
    elif target is RunStatus.CANCELLED:
        assert item.state == "open"
    else:
        assert item.state != "in_progress"


@pytest.mark.unit
def test_finish_path_emits_notifier_once_with_runner_finish_defer(
    store: RunStore,
    wi_store: WorkItemStore,
    notifier_safe_path: None,
    notify_spy: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defer ``notify_run_terminal`` on ``transition_to``, then emit once via ``handle_run_status_change``."""
    from staff import run_link
    from staff import runner_ops as ops

    item_id = _work_item_in_progress(wi_store)
    rec = RunRecord(
        "finish-1",
        "ad-hoc",
        "claude",
        None,
        "m",
        "repo",
        "prompt",
        "",
        "p",
        status="running",
        work_item_id=item_id,
        error="x",
    )
    store.create_run(rec)

    handle_calls: list[str] = []
    real_handle = run_link.handle_run_status_change

    def _capture(run: RunRecord, status: str, **kwargs: Any) -> None:
        handle_calls.append(status)
        real_handle(run, status, **kwargs)

    monkeypatch.setattr(run_link, "handle_run_status_change", _capture)

    transition_to(store, "finish-1", RunStatus.FAILED, notify_terminal=False, error="x", ended_at="t")
    assert notify_spy == []
    updated = store.get_run("finish-1")
    assert updated is not None
    ops.emit_terminal_run_status(
        store,
        updated,
        MagicMock(provider="claude", model="m"),
        status=updated.status,
        question=None,
        result_line="",
        cancelled=False,
        rc=1,
        transcript=Path("/tmp/none"),
    )
    assert handle_calls == ["failed"]
    item = wi_store.get_work_item(item_id)
    assert item is not None and item.state == "blocked"

