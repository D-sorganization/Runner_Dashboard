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
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

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
    app.add_middleware(SessionMiddleware, secret_key="test-secret")  # pragma: allowlist secret
    app.include_router(queue_router.router)
    yield TestClient(app, raise_server_exceptions=True)
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
    assert data["stats"] == {
        "repos_sampled": 1,
        "repos_succeeded": 1,
        "repos_failed": 0,
        "failed_repositories": [],
        "job_detail_failures": 0,
        "budget_exhausted": False,
        "complete": True,
    }
    assert data["data_source"] == "live"
    assert data["generated_at"]
    assert data["served_at"]


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
    app.add_middleware(SessionMiddleware, secret_key="test-secret")  # pragma: allowlist secret
    app.include_router(queue_router.router)
    client = TestClient(app, raise_server_exceptions=True)

    data = client.get("/api/queue").json()
    # Falls back to at least the run-level queued count (never silently 0).
    assert data["queued_jobs_count"] >= data["queued_count"] == 1
    assert data["stats"]["job_detail_failures"] == 1
    assert data["stats"]["complete"] is False
    assert data["data_source"] == "partial"


def test_queue_marks_partial_repository_sample_non_authoritative(monkeypatch: pytest.MonkeyPatch) -> None:
    """A partial sample must never look like an authoritative empty queue."""
    from routers import queue as queue_router  # noqa: PLC0415

    monkeypatch.setattr(queue_router, "cache_get", lambda *a, **k: None)
    monkeypatch.setattr(queue_router, "cache_set", lambda *a, **k: None)

    async def fake_gh_api(url: str) -> Any:
        if "/orgs/" in url and "/repos" in url:
            return [{"name": "RepoA"}, {"name": "RepoB"}]
        if "/RepoB/" in url:
            raise RuntimeError("rate limited")
        if "status=" in url:
            return {"workflow_runs": []}
        return {"jobs": []}

    monkeypatch.setattr(queue_router, "_gh_api", fake_gh_api)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")  # pragma: allowlist secret
    app.include_router(queue_router.router)

    data = TestClient(app).get("/api/queue").json()
    assert data["queued_jobs_count"] == 0
    assert data["stats"]["complete"] is False
    assert data["stats"]["repos_succeeded"] == 1
    assert data["stats"]["failed_repositories"] == ["RepoB"]
    assert data["data_source"] == "partial"


def test_queue_refresh_budget_returns_partial_when_repository_stalls(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single stalled repository must not hold the queue endpoint open."""
    from routers import queue as queue_router  # noqa: PLC0415

    monkeypatch.setattr(queue_router, "cache_get", lambda *a, **k: None)
    monkeypatch.setattr(queue_router, "cache_set", lambda *a, **k: None)
    monkeypatch.setattr(queue_router, "_QUEUE_REFRESH_BUDGET_SECONDS", 0.05)
    monkeypatch.setattr(queue_router, "_QUEUE_REPO_CONCURRENCY", 2)

    async def fake_gh_api(url: str) -> Any:
        if "/orgs/" in url and "/repos" in url:
            return [{"name": "RepoA"}, {"name": "RepoB"}]
        if "/RepoB/" in url:
            import asyncio  # noqa: PLC0415

            await asyncio.sleep(1)
        return {"workflow_runs": []}

    monkeypatch.setattr(queue_router, "_gh_api", fake_gh_api)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")  # pragma: allowlist secret
    app.include_router(queue_router.router)

    started = time.monotonic()
    data = TestClient(app).get("/api/queue").json()
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert data["data_source"] == "partial"
    assert data["stats"]["budget_exhausted"] is True
    assert data["stats"]["repos_succeeded"] == 1
    assert data["stats"]["failed_repositories"] == ["RepoB"]


def test_queue_reuses_run_keyed_job_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated refreshes reuse per-run job counts instead of re-downloading."""
    from routers import queue as queue_router  # noqa: PLC0415

    job_cache: dict[str, Any] = {}

    def fake_cache_get(key: str, _ttl: float) -> Any:
        return job_cache.get(key) if key.startswith("queue:jobs:") else None

    def fake_cache_set(key: str, value: Any) -> None:
        if key.startswith("queue:jobs:"):
            job_cache[key] = value

    monkeypatch.setattr(queue_router, "cache_get", fake_cache_get)
    monkeypatch.setattr(queue_router, "cache_set", fake_cache_set)
    job_calls = 0

    async def fake_gh_api(url: str) -> Any:
        nonlocal job_calls
        if "/orgs/" in url and "/repos" in url:
            return [{"name": "RepoA"}]
        if "/jobs" in url:
            job_calls += 1
            return _jobs_payload(["queued"])
        if "status=queued" in url:
            return {"workflow_runs": [_run("RepoA", 100, "queued")]}
        return {"workflow_runs": []}

    monkeypatch.setattr(queue_router, "_gh_api", fake_gh_api)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")  # pragma: allowlist secret
    app.include_router(queue_router.router)
    client = TestClient(app)

    assert client.get("/api/queue").json()["queued_jobs_count"] == 1
    assert client.get("/api/queue").json()["queued_jobs_count"] == 1
    assert job_calls == 1


def test_queue_refresh_budget_bounds_stalled_job_details(monkeypatch: pytest.MonkeyPatch) -> None:
    """Job enrichment falls back to the run-level lower bound at deadline."""
    from routers import queue as queue_router  # noqa: PLC0415

    monkeypatch.setattr(queue_router, "cache_get", lambda *a, **k: None)
    monkeypatch.setattr(queue_router, "cache_set", lambda *a, **k: None)
    monkeypatch.setattr(queue_router, "_QUEUE_REFRESH_BUDGET_SECONDS", 0.05)

    async def fake_gh_api(url: str) -> Any:
        if "/orgs/" in url and "/repos" in url:
            return [{"name": "RepoA"}]
        if "/jobs" in url:
            import asyncio  # noqa: PLC0415

            await asyncio.sleep(1)
        if "status=queued" in url:
            return {"workflow_runs": [_run("RepoA", 100, "queued")]}
        return {"workflow_runs": []}

    monkeypatch.setattr(queue_router, "_gh_api", fake_gh_api)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")  # pragma: allowlist secret
    app.include_router(queue_router.router)

    started = time.monotonic()
    data = TestClient(app).get("/api/queue").json()

    assert time.monotonic() - started < 0.5
    assert data["queued_jobs_count"] == data["queued_count"] == 1
    assert data["stats"]["job_detail_failures"] == 1
    assert data["stats"]["budget_exhausted"] is True
    assert data["data_source"] == "partial"
