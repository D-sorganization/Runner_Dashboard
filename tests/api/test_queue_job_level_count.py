"""Tests for job-level queue depth in GET /api/queue.

Root cause (Runner_Dashboard queue undercount): GitHub Actions queues at the
JOB level, but ``/api/queue`` historically counted only at the workflow-RUN
level using ``?status=queued``. A multi-job run flips its run-level status to
``in_progress`` the instant its first job starts, while sibling jobs remain
``queued`` — those queued jobs then vanish from the dashboard's count.

These tests pin the additive ``queued_jobs_count`` field, which reflects true
job-level queue depth (including queued jobs nested inside ``in_progress``
runs), while leaving the legacy run-level ``queued_count`` untouched.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")


def _run(repo: str, run_id: int, status: str) -> dict[str, Any]:
    return {
        "id": run_id,
        "status": status,
        "name": "ci",
        "created_at": "2026-05-29T10:00:00Z",
        "run_started_at": "2026-05-29T10:01:00Z",
        "repository": {"name": repo},
    }


def _jobs_payload(statuses: list[str]) -> dict[str, list[dict[str, Any]]]:
    return {"jobs": [{"id": i, "status": s} for i, s in enumerate(statuses)]}


@pytest.fixture
def queue_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Build a tiny app around the queue router and stub gh api calls."""
    from routers import queue as queue_router  # noqa: PLC0415

    # Avoid serving cached results from a previous test run.
    monkeypatch.setattr(queue_router, "cache_get", lambda *a, **k: None)
    monkeypatch.setattr(queue_router, "cache_set", lambda *a, **k: None)

    # One repo with two active runs:
    #   - run 100: run-level "queued"      -> 1 queued job
    #   - run 200: run-level "in_progress" -> but 2 of its 3 jobs are STILL queued
    # Run-level queued_count == 1; true job-level queued depth == 3.
    async def fake_gh_api(url: str) -> Any:
        if "/orgs/" in url and "/repos" in url:
            return [{"name": "RepoA"}]
        if "/jobs" in url:
            if "/100/jobs" in url:
                return _jobs_payload(["queued"])
            if "/200/jobs" in url:
                return _jobs_payload(["in_progress", "queued", "queued"])
            return _jobs_payload([])
        if "status=queued" in url:
            return {"workflow_runs": [_run("RepoA", 100, "queued")]}
        if "status=in_progress" in url:
            return {"workflow_runs": [_run("RepoA", 200, "in_progress")]}
        return {"workflow_runs": []}

    async def fail_run_cmd(*_args: Any, **_kwargs: Any) -> tuple[int, str, str]:
        raise AssertionError("/api/queue read path must not shell out through gh")

    monkeypatch.setattr(queue_router, "_gh_api", fake_gh_api)
    monkeypatch.setattr(queue_router, "run_cmd", fail_run_cmd)

    app = FastAPI()
    app.include_router(queue_router.router)
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_queue_surfaces_job_level_queued_depth(queue_app: TestClient) -> None:
    resp = queue_app.get("/api/queue")
    assert resp.status_code == 200
    data = resp.json()

    # Legacy run-level count is unchanged (1 run with run-level status=queued).
    assert data["queued_count"] == 1

    # New field reflects true job-level depth: 1 queued job in the queued run
    # PLUS 2 queued jobs hidden inside the in_progress run.
    assert data["queued_jobs_count"] == 3


def test_queue_job_count_falls_back_when_jobs_fetch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the per-run jobs fetch fails, we must not zero the queued depth."""
    from routers import queue as queue_router  # noqa: PLC0415

    monkeypatch.setattr(queue_router, "cache_get", lambda *a, **k: None)
    monkeypatch.setattr(queue_router, "cache_set", lambda *a, **k: None)

    async def fake_gh_api(url: str) -> Any:
        if "/orgs/" in url and "/repos" in url:
            return [{"name": "RepoA"}]
        if "/jobs" in url:
            raise RuntimeError("boom")  # jobs fetch fails
        if "status=queued" in url:
            return {"workflow_runs": [_run("RepoA", 100, "queued")]}
        if "status=in_progress" in url:
            return {"workflow_runs": []}
        return {"workflow_runs": []}

    async def fail_run_cmd(*_args: Any, **_kwargs: Any) -> tuple[int, str, str]:
        raise AssertionError("/api/queue read path must not shell out through gh")

    monkeypatch.setattr(queue_router, "_gh_api", fake_gh_api)
    monkeypatch.setattr(queue_router, "run_cmd", fail_run_cmd)

    app = FastAPI()
    app.include_router(queue_router.router)
    client = TestClient(app, raise_server_exceptions=False)

    data = client.get("/api/queue").json()
    # Falls back to at least the run-level queued count (never silently 0).
    assert data["queued_jobs_count"] >= data["queued_count"] == 1
