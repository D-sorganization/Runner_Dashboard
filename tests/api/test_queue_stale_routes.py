"""Focused tests for stale queue cleanup API routes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")


@pytest.fixture
def queue_route_context(tmp_path, monkeypatch):
    """Build a tiny app around the queue router without importing server.py."""
    monkeypatch.chdir(tmp_path)

    from identity import Principal, require_principal  # noqa: PLC0415
    from queue_cleanup import StaleRun  # noqa: PLC0415
    from routers import queue as queue_router  # noqa: PLC0415

    app = FastAPI()
    app.include_router(queue_router.router)

    def make_client(role: str = "operator") -> TestClient:
        principal = Principal(id=f"test-{role}", type="bot", name=f"Test {role}", roles=[role])
        app.dependency_overrides[require_principal] = lambda: principal
        return TestClient(app, raise_server_exceptions=False)

    yield app, queue_router, StaleRun, make_client
    app.dependency_overrides.clear()


def test_get_stale_queue_route_returns_annotated_runs(queue_route_context, monkeypatch) -> None:
    _app, queue_router, StaleRun, make_client = queue_route_context

    async def fake_find_stale_runs(_org: str, min_age_minutes: int) -> list[StaleRun]:
        assert min_age_minutes == 90
        return [
            StaleRun(
                repo="repo",
                run_id=10,
                workflow="CI",
                branch="feature",
                created_at="2026-04-01T10:00:00Z",
                age_minutes=120,
                event="pull_request",
                head_sha="old",
                pull_request_number=5,
                current_pr_head_sha="new",
                pr_head_superseded=True,
                supersession_reason="pr-head-advanced",
            )
        ]

    monkeypatch.setattr(queue_router, "find_stale_runs", fake_find_stale_runs)
    client = make_client()

    response = client.get("/api/queue/stale?min_age_minutes=90")

    assert response.status_code == 200
    payload = response.json()
    assert payload["stale_count"] == 1
    assert payload["superseded_count"] == 1
    assert payload["runs"][0]["pr_head_superseded"] is True
    assert payload["runs"][0]["supersession_reason"] == "pr-head-advanced"


def test_purge_stale_requires_workflows_control(queue_route_context) -> None:
    _app, _queue_router, _StaleRun, make_client = queue_route_context
    client = make_client("viewer")

    response = client.post("/api/queue/purge-stale")

    assert response.status_code == 403
    assert response.json()["detail"]["required_scope"] == "workflows.control"


def test_purge_stale_route_defaults_to_dry_run_and_superseded_only(
    queue_route_context,
    monkeypatch,
) -> None:
    _app, queue_router, _StaleRun, make_client = queue_route_context

    async def fake_purge_stale_runs(
        _org: str,
        *,
        min_age_minutes: int,
        dry_run: bool,
        superseded_only: bool,
        **_filters,
    ) -> dict:
        return {
            "org": _org,
            "min_age_minutes": min_age_minutes,
            "dry_run": dry_run,
            "superseded_only": superseded_only,
            "stale_count": 1,
            "purge_candidate_count": 1,
            "cancelled_count": 0,
            "errors": [],
            "runs": [],
        }

    monkeypatch.setattr(queue_router, "purge_stale_runs", fake_purge_stale_runs)
    client = make_client("operator")

    response = client.post("/api/queue/purge-stale")

    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert response.json()["superseded_only"] is True


def test_purge_stale_route_invalidates_queue_caches_when_cancelled(
    queue_route_context,
    monkeypatch,
) -> None:
    _app, queue_router, _StaleRun, make_client = queue_route_context
    deleted: list[str] = []

    async def fake_purge_stale_runs(
        _org: str,
        *,
        min_age_minutes: int,
        dry_run: bool,
        superseded_only: bool,
        **_filters,
    ) -> dict:
        return {
            "org": _org,
            "min_age_minutes": min_age_minutes,
            "dry_run": dry_run,
            "superseded_only": superseded_only,
            "stale_count": 1,
            "purge_candidate_count": 1,
            "cancelled_count": 1,
            "errors": [],
            "runs": [],
        }

    monkeypatch.setattr(queue_router, "purge_stale_runs", fake_purge_stale_runs)
    monkeypatch.setattr(queue_router, "cache_delete", deleted.append)
    client = make_client("operator")

    response = client.post("/api/queue/purge-stale?dry_run=false")

    assert response.status_code == 200
    assert response.json()["cancelled_count"] == 1
    assert deleted == ["queue", "queue:stale", "diagnose"]


def test_purge_stale_route_accepts_queue_ui_json_payload(queue_route_context, monkeypatch) -> None:
    _app, queue_router, _StaleRun, make_client = queue_route_context
    observed: dict = {}

    async def fake_purge_stale_runs(
        _org: str,
        *,
        min_age_minutes: int,
        dry_run: bool,
        superseded_only: bool,
        repo: str | None,
        workflow: str | None,
        reason: str | None,
        safe_only: bool,
        max_count: int | None,
    ) -> dict:
        observed.update(
            {
                "min_age_minutes": min_age_minutes,
                "dry_run": dry_run,
                "superseded_only": superseded_only,
                "repo": repo,
                "workflow": workflow,
                "reason": reason,
                "safe_only": safe_only,
                "max_count": max_count,
            }
        )
        return {
            "org": _org,
            "min_age_minutes": min_age_minutes,
            "dry_run": dry_run,
            "superseded_only": superseded_only,
            "stale_count": 2,
            "purge_candidate_count": 1,
            "cancelled_count": 0,
            "errors": [],
            "runs": [],
        }

    monkeypatch.setattr(queue_router, "purge_stale_runs", fake_purge_stale_runs)
    client = make_client("operator")

    response = client.post(
        "/api/queue/purge-stale",
        json={
            "min_age_minutes": 120,
            "dry_run": False,
            "repo": "Runner_Dashboard",
            "workflow": "CI Standard",
            "reason": "superseded_pr_head",
            "safe_only": True,
            "max_count": 10,
        },
    )

    assert response.status_code == 200
    assert observed == {
        "min_age_minutes": 120,
        "dry_run": False,
        "superseded_only": True,
        "repo": "Runner_Dashboard",
        "workflow": "CI Standard",
        "reason": "superseded_pr_head",
        "safe_only": True,
        "max_count": 10,
    }
