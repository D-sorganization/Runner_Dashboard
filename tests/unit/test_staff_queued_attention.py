"""Workerless queued runs must be visible without alerting on normal backlog."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from staff.runner import StaffRunner
from staff.store import RunRecord, RunStore

pytestmark = pytest.mark.unit
NOW = datetime(2026, 11, 16, 18, tzinfo=UTC)


def _run(**updates: Any) -> RunRecord:
    record = RunRecord(
        id="run-stale-queued",
        role="worker",
        provider="codex",
        model=None,
        machine="Desk",
        repo="Tools",
        target_kind="issue",
        target_ref="#100",
        prompt="test",
        created_at=(NOW - timedelta(minutes=15)).isoformat(),
    )
    return replace(record, **updates)


def test_stale_workerless_queue_has_actionable_attention_without_mutating_run() -> None:
    from staff.queued_attention import format_stale_queued_attention  # noqa: PLC0415

    record = _run()
    items = format_stale_queued_attention([record], machine="Desk", active_worker_ids=frozenset(), now=NOW)

    assert len(items) == 1
    assert items[0]["id"] == record.id
    assert items[0]["status"] == "queued"
    assert items[0]["failure_class"] == "orphaned_queued"
    assert items[0]["retryable"] is True
    assert items[0]["queued_seconds"] == 900
    assert "worker" in items[0]["error"]
    assert record.status == "queued"
    assert record.failure_class == ""


@pytest.mark.parametrize(
    "updates",
    [
        {"status": "running"},
        {"status": "failed"},
        {"machine": "Other"},
        {"started_at": NOW.isoformat()},
        {"pid": 123},
        {"next_attempt_at": (NOW + timedelta(minutes=1)).isoformat()},
        {"created_at": NOW.isoformat()},
        {"created_at": (NOW - timedelta(minutes=10)).isoformat()},
        {"created_at": "bad timestamp"},
        {"created_at": ""},
    ],
)
def test_non_orphan_queue_cases_do_not_raise_attention(updates: dict[str, Any]) -> None:
    from staff.queued_attention import format_stale_queued_attention  # noqa: PLC0415

    assert (
        format_stale_queued_attention(
            [_run(**updates)],
            machine="Desk",
            active_worker_ids=frozenset(),
            now=NOW,
        )
        == []
    )


def test_live_worker_prevents_a_stale_queue_false_alarm() -> None:
    from staff.queued_attention import format_stale_queued_attention  # noqa: PLC0415

    record = _run()
    assert (
        format_stale_queued_attention(
            [record],
            machine="Desk",
            active_worker_ids=frozenset({record.id}),
            now=NOW,
        )
        == []
    )


def test_naive_persisted_timestamp_is_interpreted_as_utc() -> None:
    from staff.queued_attention import format_stale_queued_attention  # noqa: PLC0415

    items = format_stale_queued_attention(
        [_run(created_at="2026-11-16T17:30:00")],
        machine="Desk",
        active_worker_ids=frozenset(),
        now=NOW,
    )
    assert items[0]["queued_seconds"] == 1800


@pytest.mark.parametrize(("overdue_seconds", "expected"), [(599, False), (601, True)])
def test_retry_backoff_only_defers_attention_until_it_is_overdue(overdue_seconds: int, expected: bool) -> None:
    from staff.queued_attention import format_stale_queued_attention  # noqa: PLC0415

    record = _run(next_attempt_at=(NOW - timedelta(seconds=overdue_seconds)).isoformat())
    items = format_stale_queued_attention([record], machine="Desk", active_worker_ids=frozenset(), now=NOW)

    assert bool(items) is expected


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf")])
def test_stale_threshold_must_be_positive_and_finite(seconds: float) -> None:
    from staff.queued_attention import format_stale_queued_attention  # noqa: PLC0415

    with pytest.raises(ValueError):
        format_stale_queued_attention(
            [], machine="Desk", active_worker_ids=frozenset(), now=NOW, stale_after_seconds=seconds
        )


def test_runner_reports_only_live_run_worker_ids(tmp_path: Path) -> None:
    runner = StaffRunner(store=RunStore(tmp_path / "runs.sqlite3"), machine="Desk")
    live, stopped, unrelated = Mock(), Mock(), Mock()
    live.name, stopped.name, unrelated.name = "staff-run-live", "staff-run-stopped", "unrelated"
    live.is_alive.return_value = unrelated.is_alive.return_value = True
    stopped.is_alive.return_value = False
    runner._threads = [live, stopped, unrelated]
    try:
        assert runner.active_worker_run_ids() == frozenset({"run-live"})
    finally:
        runner.store.close()
