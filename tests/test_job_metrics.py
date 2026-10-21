"""Storage, aggregation, and bounded collection tests for job timing metrics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import gh_client
import job_metrics
import job_metrics_collector
import pytest


@pytest.fixture
def stats_db(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STATS_DB_PATH", str(tmp_path / "stats.db"))


def _run(*, attempt: Any = 1, run_id: int = 21) -> dict[str, Any]:
    payload = {
        "id": run_id,
        "name": "CI",
        "run_attempt": attempt,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "updated_at": "2026-10-21T11:00:00Z",
    }
    return payload


def _job(job_id: int = 31, *, attempt: int | None = 1, labels: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": job_id,
        "run_attempt": attempt,
        "name": "tests",
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-10-21T10:00:00Z",
        "started_at": "2026-10-21T10:00:10Z",
        "completed_at": "2026-10-21T10:01:10Z",
        "runner_name": "d-sorg-fleet-DeskComputer-1",
        "labels": labels or ["self-hosted", "linux"],
    }


def test_query_separates_metric_sample_counts_and_label_fanout(stats_db: None) -> None:
    job_metrics.save_job_batch(
        "D-sorganization",
        "Runner_Dashboard",
        _run(),
        [_job(labels=["linux", "self-hosted", "linux"])],
        complete=True,
        observed_at="2026-10-21T11:05:00+00:00",
        run_updated_at="2026-10-21T11:00:00Z",
    )

    summary = job_metrics.get_job_metrics(14, "label", now=datetime(2026, 10, 21, 12, tzinfo=UTC))

    assert summary["sample"] == {"jobs": 1, "queue": 1, "execution": 1}
    assert summary["labels_are_non_additive"] is True
    assert {row["key"] for row in summary["rows"]} == {"linux", "self-hosted"}
    assert all(row["job_count"] == 1 and row["queue_p50_seconds"] == 10 for row in summary["rows"])
    assert all(row["execute_p50_seconds"] == 60 for row in summary["rows"])


def test_query_returns_empty_percentiles_and_validates_filters(stats_db: None) -> None:
    summary = job_metrics.get_job_metrics(7, "host", now=datetime(2026, 10, 21, tzinfo=UTC))
    assert summary["rows"] == []
    assert summary["sample"] == {"jobs": 0, "queue": 0, "execution": 0}
    with pytest.raises(ValueError):
        job_metrics.get_job_metrics(0, "repo")
    with pytest.raises(ValueError):
        job_metrics.get_job_metrics(7, "workflow")  # type: ignore[arg-type]


def test_job_upsert_moves_incomplete_attempt_to_complete_without_duplicate(stats_db: None) -> None:
    initial = _job()
    initial.update(status="in_progress", completed_at=None)
    job_metrics.save_job_batch(
        "D-sorganization",
        "Runner_Dashboard",
        _run(),
        [initial],
        complete=False,
        observed_at="2026-10-21T10:01:00+00:00",
        run_updated_at=None,
    )
    completed = _job()
    job_metrics.save_job_batch(
        "D-sorganization",
        "Runner_Dashboard",
        _run(),
        [completed],
        complete=True,
        observed_at="2026-10-21T10:02:00+00:00",
        run_updated_at="2026-10-21T10:01:10Z",
    )

    with job_metrics.connect_stats_db() as conn:
        row = conn.execute("SELECT status,run_attempt,execute_seconds FROM workflow_jobs").fetchone()
        progress = conn.execute("SELECT collection_complete FROM workflow_job_runs").fetchone()
        count = conn.execute("SELECT COUNT(*) FROM workflow_jobs").fetchone()[0]
    assert (row["status"], row["run_attempt"], row["execute_seconds"]) == ("completed", 1, 60)
    assert progress["collection_complete"] == 1
    assert count == 1


def test_attempt_parser_rejects_non_integer_values() -> None:
    assert job_metrics.positive_attempt(2) == 2
    assert job_metrics.positive_attempt(" 3 ") == 3
    assert job_metrics.positive_attempt(True) is None
    assert job_metrics.positive_attempt(2.5) is None
    assert job_metrics.positive_attempt("0") is None


@pytest.mark.asyncio
async def test_collector_stores_attempt_and_completes_bounded_cycle(
    stats_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_get(path: str) -> Any:
        if path.startswith("/orgs/"):
            return [{"name": "Runner_Dashboard"}]
        if "/actions/runs?" in path:
            return {"workflow_runs": [_run(attempt=2)]}
        if "/runs/21/jobs?" in path:
            assert "filter=all" in path
            return {"jobs": [_job(attempt=2)]}
        raise AssertionError("unexpected endpoint")

    monkeypatch.setattr(gh_client, "get", fake_get)
    result = await job_metrics_collector.collect_job_metrics("D-sorganization", max_pages=4)

    assert result["complete"] is True
    assert result["runs"] == 1
    assert result["jobs"] == 1
    with job_metrics.connect_stats_db() as conn:
        row = conn.execute("SELECT run_attempt,created_at FROM workflow_jobs").fetchone()
    assert row["run_attempt"] == 2
    assert row["created_at"] == "2026-10-21T10:00:00+00:00"


@pytest.mark.asyncio
async def test_filter_all_preserves_each_job_attempt_not_parent_latest(
    stats_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_get(path: str) -> Any:
        if path.startswith("/orgs/"):
            return [{"name": "Runner_Dashboard"}]
        if "/actions/runs?" in path:
            return {"workflow_runs": [_run(attempt=2)]}
        if "/runs/21/jobs?" in path:
            assert "filter=all" in path
            return {"jobs": [_job(31, attempt=1), _job(32, attempt=2)]}
        raise AssertionError("unexpected endpoint")

    monkeypatch.setattr(gh_client, "get", fake_get)
    await job_metrics_collector.collect_job_metrics("D-sorganization", max_pages=4)
    with job_metrics.connect_stats_db() as conn:
        attempts = conn.execute("SELECT job_id,run_attempt FROM workflow_jobs ORDER BY job_id").fetchall()
    assert [(row["job_id"], row["run_attempt"]) for row in attempts] == [(31, 1), (32, 2)]


@pytest.mark.asyncio
async def test_completed_unchanged_run_skips_job_refetch_but_in_progress_does_not(
    stats_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"jobs": 0}
    current_run = _run()

    async def fake_get(path: str) -> Any:
        if path.startswith("/orgs/"):
            return [{"name": "Runner_Dashboard"}]
        if "/actions/runs?" in path:
            return {"workflow_runs": [current_run]}
        if "/runs/21/jobs?" in path:
            calls["jobs"] += 1
            return {"jobs": [_job()]}
        raise AssertionError("unexpected endpoint")

    monkeypatch.setattr(gh_client, "get", fake_get)
    await job_metrics_collector.collect_job_metrics("D-sorganization", max_pages=4)
    await job_metrics_collector.collect_job_metrics("D-sorganization", max_pages=4)
    assert calls["jobs"] == 1

    current_run = {**current_run, "status": "in_progress", "conclusion": None}
    await job_metrics_collector.collect_job_metrics("D-sorganization", max_pages=4)
    assert calls["jobs"] == 2


@pytest.mark.asyncio
async def test_collector_does_not_invent_missing_attempt_and_reports_partial(
    stats_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_get(path: str) -> Any:
        if path.startswith("/orgs/"):
            return [{"name": "Runner_Dashboard"}]
        if "/actions/runs?" in path:
            return {"workflow_runs": [_run(attempt=None)]}
        if "/runs/21/jobs?" in path:
            job = _job()
            job.pop("run_attempt")
            return {"jobs": [job]}
        raise AssertionError("unexpected endpoint")

    monkeypatch.setattr(gh_client, "get", fake_get)
    result = await job_metrics_collector.collect_job_metrics("D-sorganization", max_pages=4)

    assert result["complete"] is False
    assert "job_run_attempt_missing_or_invalid" in result["partial_reasons"]
    with job_metrics.connect_stats_db() as conn:
        row = conn.execute("SELECT latest_attempt,collection_complete FROM workflow_job_runs").fetchone()
    assert row["latest_attempt"] is None
    assert row["collection_complete"] == 0
    with job_metrics.connect_stats_db() as conn:
        job = conn.execute("SELECT run_attempt FROM workflow_jobs").fetchone()
    assert job["run_attempt"] is None


@pytest.mark.asyncio
async def test_page_cap_keeps_cursor_on_unfinished_run_then_resumes(
    stats_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_get(path: str) -> Any:
        if path.startswith("/orgs/"):
            return [{"name": "Runner_Dashboard"}]
        if "/actions/runs?" in path:
            return {"workflow_runs": [_run()]}
        if "/runs/21/jobs?" in path:
            assert "filter=all" in path
            return {"jobs": [_job()]}
        raise AssertionError("unexpected endpoint")

    monkeypatch.setattr(gh_client, "get", fake_get)
    first = await job_metrics_collector.collect_job_metrics("D-sorganization", max_pages=2)
    assert first["complete"] is False
    assert "page_cap" in first["caps"]
    assert first["cursor"]["run_offset"] == 0

    second = await job_metrics_collector.collect_job_metrics("D-sorganization", max_pages=10)
    assert second["complete"] is True, second
    assert second["jobs"] == 1
    with job_metrics.connect_stats_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM workflow_jobs").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_collector_stops_on_rate_limit_and_records_retry_metadata(
    stats_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_get(path: str) -> Any:
        if path.startswith("/orgs/"):
            return [{"name": "Runner_Dashboard"}]
        if "/actions/runs?" in path:
            return {"workflow_runs": [_run()]}
        raise gh_client.GhRateLimited(retry_after_seconds=19, endpoint="jobs")

    monkeypatch.setattr(gh_client, "get", fake_get)
    result = await job_metrics_collector.collect_job_metrics("D-sorganization", max_pages=4)
    assert result["complete"] is False
    assert result["retry_after_seconds"] == 19
    assert result["partial_reasons"] == ["rate_limited"]
    assert result["cursor"]["run_offset"] == 0


@pytest.mark.asyncio
async def test_collector_reports_malformed_persisted_cursor(stats_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job_metrics_collector, "load_collection_state", lambda _org: {"repo_page": True})

    async def fake_get(path: str) -> Any:
        assert path.startswith("/orgs/")
        return []

    monkeypatch.setattr(gh_client, "get", fake_get)
    result = await job_metrics_collector.collect_job_metrics("D-sorganization", max_pages=1)

    assert result["complete"] is False
    assert result["partial_reasons"] == ["persisted_cursor_invalid"]


@pytest.mark.asyncio
async def test_job_page_cap_resumes_and_idempotently_finishes_attempt(
    stats_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_pages = [[_job(index + 100) for index in range(100)]]
    job_pages.append([_job(index + 200) for index in range(100)])
    job_pages.append([_job(index + 300) for index in range(50)])
    job_pages[0][0].pop("run_attempt")
    requested_job_pages: list[int] = []

    async def fake_get(path: str) -> Any:
        if path.startswith("/orgs/"):
            return [{"name": "Runner_Dashboard"}]
        if "/actions/runs?" in path:
            return {"workflow_runs": [_run()]}
        if "/runs/21/jobs?" in path:
            assert "filter=all" in path
            page = parse_qs(urlparse(path).query)["page"][0]
            requested_job_pages.append(int(page))
            return {"jobs": job_pages[int(page) - 1]}
        raise AssertionError("unexpected endpoint")

    monkeypatch.setattr(gh_client, "get", fake_get)
    first = await job_metrics_collector.collect_job_metrics("D-sorganization", max_pages=3)
    assert first["complete"] is False
    assert first["pages"] == 3
    assert first["cursor"]["job_page"] == 2
    with job_metrics.connect_stats_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM workflow_jobs").fetchone()[0] == 100

    second = await job_metrics_collector.collect_job_metrics("D-sorganization", max_pages=3)
    assert second["complete"] is False
    assert second["pages"] == 3
    assert second["cursor"]["job_page"] == 3
    with job_metrics.connect_stats_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM workflow_jobs").fetchone()[0] == 200

    third = await job_metrics_collector.collect_job_metrics("D-sorganization", max_pages=4)
    assert third["complete"] is False, third
    assert third["traversal_complete"] is True
    assert third["pages"] == 3
    assert requested_job_pages == [1, 2, 3]
    with job_metrics.connect_stats_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM workflow_jobs").fetchone()[0] == 250
        progress = conn.execute("SELECT collection_complete,collection_reasons_json FROM workflow_job_runs").fetchone()
    assert progress["collection_complete"] == 0
    assert "job_run_attempt_missing_or_invalid" in progress["collection_reasons_json"]
    summary = job_metrics.get_job_metrics(14, "repo")
    assert summary["coverage"]["incomplete_runs"] == 1
    assert "job_run_attempt_missing_or_invalid" in summary["coverage"]["partial_reasons"]


def test_coverage_exposes_capped_repository_state(stats_db: None) -> None:
    job_metrics.save_collection_state(
        "D-sorganization",
        {"complete": False, "partial_reasons": [], "caps": ["run_cap"]},
    )
    result = job_metrics.get_job_metrics(14, "repo")
    assert result["coverage"] == {
        "complete": False,
        "organizations_reported": 1,
        "partial_reasons": [],
        "caps": ["run_cap"],
        "incomplete_runs": 0,
    }
