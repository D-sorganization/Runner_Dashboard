"""Tests for merge-queue batching visibility and observed group metrics (issue #3293, slice 4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import gh_client
import pr_inventory
import pytest
from coordination.overload import build_overload_snapshot, reset_drain_state
from coordination.queue_batching import (
    build_queue_batching_summary,
    compute_observed_group_metrics,
    fetch_completed_merge_group_runs,
)
from delivery_metrics.adapters.github import GhClientDeliveryCollector
from delivery_metrics.models import PrSnapshot

REPO = "D-sorganization/Runner_Dashboard"
SAMPLE_SETTINGS = {
    "max_entries_to_build": 5,
    "max_entries_to_merge": 2,
    "min_entries_to_merge": 1,
    "min_entries_to_merge_wait_minutes": 5,
    "check_response_timeout_minutes": 60,
}


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_drain_state()

    async def _fake_fetch_repo_prs(_repo: str) -> tuple[list[dict[str, Any]], str | None]:
        return [], None

    async def _fake_list_pulls(
        self: Any, _repo: str, _since: datetime, *, max_pages: int = 3
    ) -> tuple[PrSnapshot, ...]:
        return ()

    async def _fake_get(_path: str) -> Any:
        return []

    async def _empty_paginate(_path: str):
        return
        yield

    monkeypatch.setattr(pr_inventory, "fetch_repo_prs", _fake_fetch_repo_prs)
    monkeypatch.setattr(GhClientDeliveryCollector, "list_pulls_updated_since", _fake_list_pulls)
    monkeypatch.setattr(gh_client, "get", _fake_get)
    monkeypatch.setattr(gh_client, "paginate", _empty_paginate)


def test_compute_observed_group_metrics_groups_runs_by_head_sha() -> None:
    now = datetime(2026, 10, 28, 20, 0, 0, tzinfo=UTC)
    runs = [
        {
            "id": 101,
            "head_sha": "sha-a",
            "run_started_at": "2026-10-28T19:00:00Z",
            "updated_at": "2026-10-28T19:08:00Z",
        },
        {
            "id": 102,
            "head_sha": "sha-a",
            "run_started_at": "2026-10-28T19:01:00Z",
            "updated_at": "2026-10-28T19:10:00Z",
        },
        {"id": 201, "head_sha": "sha-b", "created_at": "2026-10-28T19:30:00Z", "updated_at": "2026-10-28T19:50:00Z"},
        {
            "id": 301,
            "head_sha": "sha-old",
            "run_started_at": "2026-10-28T10:00:00Z",
            "updated_at": "2026-10-28T10:05:00Z",
        },
        {"id": 401, "head_sha": "sha-incomplete"},
    ]

    metrics = compute_observed_group_metrics(runs, since=now - timedelta(hours=6))

    assert metrics["observed_group_count"] == 2
    assert metrics["observed_group_seconds"] == 900.0
    assert metrics["observed_group_minutes"] == 15.0
    assert metrics["estimated_groups_per_hour"] == 4.0


def test_build_queue_batching_summary_recommends_larger_group_when_queue_exceeds_batch_size() -> None:
    windows = {
        "1h": {"open_count": 8, "merge_count": 3, "open_rate_per_hour": 8.0, "merge_rate_per_hour": 3.0},
        "6h": {"open_count": 36, "merge_count": 21, "open_rate_per_hour": 6.0, "merge_rate_per_hour": 3.5},
    }
    runs = [
        {"id": 1, "head_sha": "sha-1", "run_started_at": "2026-10-28T19:00:00Z", "updated_at": "2026-10-28T19:12:00Z"}
    ]

    summary = build_queue_batching_summary(
        settings=SAMPLE_SETTINGS, windows=windows, queue_depth=7, completed_runs=runs
    )

    assert summary["read_only"] is True
    assert summary["settings"] == SAMPLE_SETTINGS
    assert summary["queue_depth"] == 7
    assert summary["merges_per_hour_1h"] == 3.0
    assert summary["merges_per_hour_6h"] == 3.5
    assert summary["open_rate_per_hour_1h"] == 8.0
    assert summary["observed_group_count"] == 1
    assert summary["observed_group_minutes"] == 12.0
    assert summary["larger_group_recommended"] is True
    assert summary["recommendation"] is not None
    assert "max_entries_to_merge (2)" in summary["recommendation"]


def test_build_queue_batching_summary_no_recommendation_when_healthy() -> None:
    windows = {
        "1h": {"open_count": 2, "merge_count": 4, "open_rate_per_hour": 2.0, "merge_rate_per_hour": 4.0},
        "6h": {"open_count": 12, "merge_count": 24, "open_rate_per_hour": 2.0, "merge_rate_per_hour": 4.0},
    }

    summary = build_queue_batching_summary(
        settings={**SAMPLE_SETTINGS, "max_entries_to_merge": 4},
        windows=windows,
        queue_depth=1,
        completed_runs=(),
    )

    assert summary["read_only"] is True
    assert summary["larger_group_recommended"] is False
    assert summary["recommendation"] is None
    assert summary["observed_group_count"] == 0
    assert summary["observed_group_minutes"] is None


def test_build_queue_batching_summary_rejects_negative_queue_depth() -> None:
    with pytest.raises(ValueError, match="queue_depth"):
        build_queue_batching_summary(settings=None, windows={}, queue_depth=-1)


@pytest.mark.asyncio
async def test_fetch_completed_merge_group_runs_degrades_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(_path: str):
        raise RuntimeError("actions api down")
        yield

    monkeypatch.setattr(gh_client, "paginate", _boom)
    runs, err = await fetch_completed_merge_group_runs(REPO)
    assert runs == []
    assert err is not None and "merge-queue group runs unavailable" in err


@pytest.mark.asyncio
async def test_build_overload_snapshot_includes_queue_batching(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    started = (now - timedelta(minutes=18)).strftime("%Y-%m-%dT%H:%M:%SZ")
    finished = (now - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def _fake_paginate(path: str):
        if "status=queued" in path:
            for i in range(5):
                yield {"id": i}
        elif "status=completed" in path:
            yield {"id": 99, "head_sha": "abc1234", "run_started_at": started, "updated_at": finished}

    async def _fake_get(path: str) -> Any:
        if path.endswith("/rulesets"):
            return [{"id": 1, "enforcement": "active"}]
        return {"rules": [{"type": "merge_queue", "parameters": SAMPLE_SETTINGS}]}

    monkeypatch.setattr(gh_client, "paginate", _fake_paginate)
    monkeypatch.setattr(gh_client, "get", _fake_get)

    snap = await build_overload_snapshot(REPO)

    batching = snap["queue_batching"]
    assert batching["read_only"] is True
    assert batching["queue_depth"] == 5
    assert batching["observed_group_count"] == 1
    assert batching["observed_group_minutes"] == 15.0
    assert batching["larger_group_recommended"] is True
    assert batching["settings"]["max_entries_to_merge"] == 2
