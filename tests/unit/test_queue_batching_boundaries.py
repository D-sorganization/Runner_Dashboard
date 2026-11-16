"""Defensive boundaries for read-only merge-group observations."""

from __future__ import annotations

from collections import UserDict
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import gh_client
import pytest
from coordination.queue_batching import (
    build_queue_batching_summary,
    compute_observed_group_metrics,
    fetch_completed_merge_group_runs,
)

REPO = "D-sorganization/Runner_Dashboard"


@pytest.mark.parametrize("repo", ["", "Runner_Dashboard"])
async def test_invalid_slug_fails_before_github(repo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_request(*args: Any, **kwargs: Any) -> None:
        pytest.fail("invalid inputs must not reach GitHub")

    monkeypatch.setattr(gh_client, "paginate", unexpected_request)
    with pytest.raises(ValueError, match="owner/name"):
        await fetch_completed_merge_group_runs(repo)


@pytest.mark.parametrize("limit", [0, -1])
async def test_nonpositive_limit_fails_before_github(limit: int, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_request(*args: Any, **kwargs: Any) -> None:
        pytest.fail("invalid inputs must not reach GitHub")

    monkeypatch.setattr(gh_client, "paginate", unexpected_request)
    with pytest.raises(ValueError, match="max_runs"):
        await fetch_completed_merge_group_runs(REPO, max_runs=limit)


async def test_pagination_skips_non_mappings_and_stops_at_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    row = UserDict({"id": 1})

    async def paginate(path: str) -> AsyncIterator[Any]:
        assert path == f"/repos/{REPO}/actions/runs?event=merge_group&status=completed&per_page=100"
        yield None
        yield row
        yield {"id": 2}
        pytest.fail("pagination must stop as soon as the requested number of rows is collected")

    monkeypatch.setattr(gh_client, "paginate", paginate)
    runs, error = await fetch_completed_merge_group_runs(REPO, max_runs=2)
    assert runs == [{"id": 1}, {"id": 2}]
    assert runs[0] is not row
    assert error is None


@pytest.mark.parametrize("not_found", [True, False])
async def test_failed_pagination_discards_partial_observations(
    not_found: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def paginate(path: str) -> AsyncIterator[Any]:
        yield {"id": 1}
        if not_found:
            raise gh_client.GhNotFound(path)
        raise RuntimeError("page two unavailable")

    monkeypatch.setattr(gh_client, "paginate", paginate)
    runs, error = await fetch_completed_merge_group_runs(REPO)
    assert runs == []
    assert error == (
        f"{REPO}: repository not found" if not_found else "merge-queue group runs unavailable: page two unavailable"
    )


@pytest.mark.parametrize(
    "run",
    [
        {"run_started_at": "not-a-date", "updated_at": "2026-11-16T10:00:00Z"},
        {"run_started_at": "2026-11-16T10:00:00Z", "updated_at": " "},
        {"run_started_at": 123, "updated_at": "2026-11-16T10:00:00Z"},
        {"run_started_at": "2026-11-16T10:01:00Z", "updated_at": "2026-11-16T10:00:00Z"},
    ],
)
def test_unusable_run_times_do_not_produce_group_metrics(run: dict[str, Any]) -> None:
    assert compute_observed_group_metrics([run]) == {
        "observed_group_count": 0,
        "observed_group_seconds": None,
        "observed_group_minutes": None,
        "estimated_groups_per_hour": None,
    }


def test_metrics_ignore_non_mapping_rows() -> None:
    runs: Any = [None, "bad row", 42]
    assert compute_observed_group_metrics(runs)["observed_group_count"] == 0


@pytest.mark.parametrize("missing_id", [{}, {"id": None}])
def test_missing_id_cannot_collide_with_explicit_id_and_normalizes_timezones(missing_id: dict[str, Any]) -> None:
    runs = [
        {"id": 1, "head_sha": " ", "created_at": "2026-11-16T10:00:00", "updated_at": "2026-11-16T10:01:00"},
        {
            **missing_id,
            "head_sha": None,
            "created_at": "2026-11-16T12:00:00+02:00",
            "updated_at": "2026-11-16T12:03:00+02:00",
        },
    ]
    assert compute_observed_group_metrics(runs, since=datetime(2026, 11, 16, 10, 1, tzinfo=UTC)) == {
        "observed_group_count": 2,
        "observed_group_seconds": 120.0,
        "observed_group_minutes": 2.0,
        "estimated_groups_per_hour": 30.0,
    }


def test_zero_duration_group_does_not_divide_by_zero() -> None:
    metrics = compute_observed_group_metrics(
        [{"created_at": "2026-11-16T10:00:00Z", "updated_at": "2026-11-16T10:00:00Z"}]
    )
    assert metrics == {
        "observed_group_count": 1,
        "observed_group_seconds": 0.0,
        "observed_group_minutes": 0.0,
        "estimated_groups_per_hour": None,
    }


@pytest.mark.parametrize("depth", [True, False, -1, 1.5, "2", None])
def test_queue_depth_requires_nonnegative_integer(depth: Any) -> None:
    with pytest.raises(ValueError, match="queue_depth"):
        build_queue_batching_summary(settings=None, windows={}, queue_depth=depth)


@pytest.mark.parametrize("maximum", [None, True, False, "2", 0, -1])
def test_unusable_batch_size_does_not_recommend_ruleset_change(maximum: Any) -> None:
    summary = build_queue_batching_summary(settings={"max_entries_to_merge": maximum}, windows={}, queue_depth=10)
    assert summary["read_only"] is True
    assert summary["larger_group_recommended"] is False
    assert summary["recommendation"] is None


@pytest.mark.parametrize(("depth", "open_rate", "expected"), [(2, 4.0, True), (2, 3.0, False), (1, 4.0, False)])
def test_rate_recommendation_requires_full_batch_and_strictly_higher_open_rate(
    depth: int, open_rate: float, expected: bool
) -> None:
    summary = build_queue_batching_summary(
        settings={"max_entries_to_merge": 2},
        windows={"1h": {"open_rate_per_hour": open_rate, "merge_rate_per_hour": 3.0}},
        queue_depth=depth,
    )
    assert summary["larger_group_recommended"] is expected
    if expected:
        assert "average group time" not in summary["recommendation"]
        assert "human ruleset action only" in summary["recommendation"]


def test_absent_settings_and_windows_have_safe_defaults() -> None:
    summary = build_queue_batching_summary(settings=None, windows={}, queue_depth=0)
    assert summary["settings"] is None
    assert summary["merges_per_hour_1h"] == summary["merges_per_hour_6h"] == summary["open_rate_per_hour_1h"] == 0.0
    assert summary["larger_group_recommended"] is False
