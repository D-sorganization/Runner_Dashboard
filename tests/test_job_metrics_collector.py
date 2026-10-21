"""Pagination boundary contracts for bounded workflow-metrics collection."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import gh_client
import job_metrics_collector
import pytest


@pytest.fixture
def stats_db(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STATS_DB_PATH", str(tmp_path / "stats.db"))


@pytest.mark.asyncio
async def test_collector_fetches_second_workflow_run_page(stats_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    requested_run_pages: list[int] = []
    processed_run_ids: list[int] = []

    async def fake_get(path: str) -> Any:
        query = parse_qs(urlparse(path).query)
        if path.startswith("/orgs/"):
            return [{"name": "repo"}]
        if "/actions/runs?" in path:
            page = int(query["page"][0])
            requested_run_pages.append(page)
            runs = [
                {
                    "id": index + 1,
                    "run_attempt": 1,
                    "status": "completed",
                    "conclusion": "success",
                    "updated_at": f"2026-10-21T00:{index % 60:02d}:00Z",
                }
                for index in range((page - 1) * 100, min(page * 100, 101))
            ]
            return {"workflow_runs": runs}
        if "/actions/runs/" in path and "/jobs?" in path:
            run_id = int(path.split("/actions/runs/")[1].split("/")[0])
            processed_run_ids.append(run_id)
            return {"jobs": []}
        raise AssertionError("unexpected fake endpoint")

    monkeypatch.setattr(gh_client, "get", fake_get)
    result = await job_metrics_collector.collect_job_metrics(
        "D-sorganization", max_repos=2, max_runs=200, max_pages=200
    )

    assert requested_run_pages == [1, 2]
    assert processed_run_ids == list(range(1, 102))
    assert result["runs"] == 101
    assert result["complete"] is True
    assert result["pages"] == 104


@pytest.mark.asyncio
async def test_collector_fetches_second_repository_page(stats_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    requested_repo_pages: list[int] = []
    listed_repositories: list[str] = []

    async def fake_get(path: str) -> Any:
        query = parse_qs(urlparse(path).query)
        if path.startswith("/orgs/"):
            page = int(query["page"][0])
            requested_repo_pages.append(page)
            start = (page - 1) * 100
            end = min(page * 100, 101)
            return [{"name": f"repo-{index}"} for index in range(start, end)]
        if "/actions/runs?" in path:
            listed_repositories.append(path.split("/repos/")[1].split("/")[1])
            return {"workflow_runs": []}
        raise AssertionError("unexpected fake endpoint")

    monkeypatch.setattr(gh_client, "get", fake_get)
    result = await job_metrics_collector.collect_job_metrics(
        "D-sorganization", max_repos=200, max_runs=2, max_pages=200
    )

    assert requested_repo_pages == [1, 2]
    assert listed_repositories == [f"repo-{index}" for index in range(101)]
    assert result["repositories"] == 101
    assert result["complete"] is True
    assert result["pages"] == 103
