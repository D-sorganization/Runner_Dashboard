"""Staff run lifecycle events feed the fleet Activity Log (issue #2859, epic #2856 slice A3)."""

from __future__ import annotations

from pathlib import Path

import activity_producers
import pytest
from activity import ActivityStore
from staff.store import RunRecord, RunStore


def _record(run_id: str = "run-1") -> RunRecord:
    return RunRecord(
        id=run_id,
        role="librarian",
        provider="claude",
        model=None,
        machine="brick",
        repo="Runner_Dashboard",
        target_kind="prompt",
        target_ref="",
        prompt="p",
        branch="claude/2859-x",
    )


@pytest.fixture(autouse=True)
def _reset_seq_counter() -> None:
    activity_producers._next_seq.clear()


@pytest.fixture
def activity_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ActivityStore:
    real_store = ActivityStore(tmp_path / "activity.sqlite3")
    monkeypatch.setattr(activity_producers, "get_activity_store", lambda: real_store)
    return real_store


@pytest.mark.unit
def test_append_event_emits_a_staff_run_activity_envelope(tmp_path: Path, activity_store: ActivityStore) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    store.create_run(_record())
    seq = store.append_event("run-1", "start", "claude in /work")
    page = activity_store.read(session_id="run-1")
    assert [item.kind for item in page.items] == ["start"]
    envelope = page.items[0]
    assert envelope.source == "staff_run"
    assert envelope.seq == seq
    assert envelope.node == "brick"
    assert envelope.repo == "Runner_Dashboard"
    assert envelope.branch == "claude/2859-x"
    assert envelope.payload == {"text": "claude in /work"}


@pytest.mark.unit
def test_append_event_reuses_its_own_seq_so_a_retry_stays_idempotent(
    tmp_path: Path, activity_store: ActivityStore
) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    store.create_run(_record())
    store.append_event("run-1", "start", "claude in /work")
    store.append_event("run-1", "exit", "exit code 0")
    page = activity_store.read(session_id="run-1")
    assert [item.seq for item in page.items] == [1, 2]


@pytest.mark.unit
def test_append_event_survives_a_broken_activity_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_store() -> ActivityStore:
        raise RuntimeError("activity store unavailable")

    monkeypatch.setattr(activity_producers, "get_activity_store", broken_store)
    store = RunStore(tmp_path / "runs.sqlite3")
    store.create_run(_record())
    seq = store.append_event("run-1", "start", "claude in /work")
    assert seq == 1
    assert store.events_after("run-1") == [
        {"seq": 1, "ts": store.events_after("run-1")[0]["ts"], "kind": "start", "text": "claude in /work"}
    ]
