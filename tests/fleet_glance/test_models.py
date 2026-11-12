from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event

import pytest

from backend.fleet_glance.models import RepoCounts, aggregate


def test_repo_counts_reject_negative_values() -> None:
    with pytest.raises(ValueError):
        RepoCounts(repo="org/repo", queued=-1)


def test_aggregate_totals_exclude_error_repos() -> None:
    healthy = RepoCounts(repo="org/a", queued=2, in_progress=1, pending=3, issues=4, prs=5)
    failed = RepoCounts(repo="org/b", queued=100, issues=100, error="forbidden")

    result = aggregate([healthy, failed], as_of=datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert result.totals == {
        "queued": 2,
        "in_progress": 1,
        "pending": 3,
        "issues": 4,
        "prs": 5,
    }
    assert result.errors == ["org/b"]
    assert result.repos == [healthy, failed]


def test_aggregate_requires_utc_timestamp() -> None:
    with pytest.raises(ValueError):
        aggregate([], as_of=datetime(2026, 1, 1))


def test_ttl_cache_bursts_do_not_multiply_github_calls() -> None:
    from backend.fleet_glance.models import TtlCache

    now = [10.0]
    cache = TtlCache(ttl_seconds=5, clock=lambda: now[0])
    calls = []
    loader_started = Event()
    release_loader = Event()

    def load() -> str:
        calls.append("load")
        loader_started.set()
        release_loader.wait(timeout=2)
        return "snapshot"

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(cache.get_or_load, "fleet", load) for _ in range(8)]
        assert loader_started.wait(timeout=2)
        release_loader.set()
        assert [future.result(timeout=2) for future in futures] == ["snapshot"] * 8
    assert calls == ["load"]

    now[0] = 16.0
    assert cache.get_or_load("fleet", load) == "snapshot"
    assert calls == ["load", "load"]


def test_issue_and_pull_request_counts_stay_separate() -> None:
    result = aggregate([RepoCounts(repo="org/repo", issues=7, prs=3)])

    assert result.totals["issues"] == 7
    assert result.totals["prs"] == 3
