"""Tests for /api/stats/workflows routes (issue #1735).

Builds a minimal FastAPI app with only the workflow_stats router included,
seeding a tmp-path SQLite DB (via STATS_DB_PATH) directly against
``workflow_stats.SCHEMA_SQL``'s ``workflow_runs`` table, mirroring the
pattern in tests/api/test_autoscaler_pools.py.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _seed_run(
    db_path: Path,
    *,
    run_id: int,
    repo: str = "alpha",
    workflow_name: str = "CI",
    conclusion: str = "success",
    created_at: str = "2026-09-20T00:00:00+00:00",
    duration_seconds: float = 120.0,
    queued_seconds: float = 5.0,
) -> None:
    import workflow_stats

    workflow_stats.init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO workflow_runs
            (run_id, repo, workflow_name, workflow_id, head_branch, event,
             status, conclusion, created_at, run_started_at, updated_at,
             queued_seconds, duration_seconds, runner_label, inserted_at)
            VALUES (?, ?, ?, 1, 'main', 'push', 'completed', ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                run_id,
                repo,
                workflow_name,
                conclusion,
                created_at,
                created_at,
                created_at,
                queued_seconds,
                duration_seconds,
                created_at,
            ),
        )


@pytest.fixture()
def stats_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "stats.db"
    monkeypatch.setenv("STATS_DB_PATH", str(db_path))
    return db_path


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    """TestClient with the workflow_stats router and a fleet-peer principal injected."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from identity import Principal, require_fleet_peer, require_scope
    from routers.workflow_stats import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    app.dependency_overrides[require_scope("workflows.control")] = lambda: Principal(
        id="test-collector", type="bot", name="Test Collector", roles=["admin"]
    )
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def test_get_summary_returns_seeded_rows(client, stats_db: Path) -> None:
    _seed_run(stats_db, run_id=1)
    resp = client.get("/api/stats/workflows")
    assert resp.status_code == 200
    data = resp.json()
    assert data["group_by"] == "workflow"
    assert len(data["rows"]) == 1
    assert data["rows"][0]["repo"] == "alpha"
    assert data["rows"][0]["workflow_name"] == "CI"
    assert data["rows"][0]["count"] == 1


def test_get_summary_group_by_repo(client, stats_db: Path) -> None:
    _seed_run(stats_db, run_id=1, repo="alpha")
    _seed_run(stats_db, run_id=2, repo="alpha", workflow_name="Other")
    resp = client.get("/api/stats/workflows", params={"group_by": "repo"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["group_by"] == "repo"
    assert data["rows"][0]["count"] == 2


def test_get_summary_days_zero_rejected(client) -> None:
    resp = client.get("/api/stats/workflows", params={"days": 0})
    assert resp.status_code == 422


def test_get_summary_days_too_large_rejected(client) -> None:
    resp = client.get("/api/stats/workflows", params={"days": 91})
    assert resp.status_code == 422


def test_get_timeseries_returns_seeded_bucket(client, stats_db: Path) -> None:
    _seed_run(stats_db, run_id=1)
    resp = client.get("/api/stats/workflows/timeseries")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["series"]) == 1
    assert data["series"][0]["count"] == 1


def test_get_timeseries_filters_by_repo(client, stats_db: Path) -> None:
    _seed_run(stats_db, run_id=1, repo="alpha")
    _seed_run(stats_db, run_id=2, repo="beta")
    resp = client.get("/api/stats/workflows/timeseries", params={"repo": "alpha"})
    assert resp.status_code == 200
    data = resp.json()
    assert sum(bucket["count"] for bucket in data["series"]) == 1


def test_get_timeseries_bucket_hours_zero_rejected(client) -> None:
    resp = client.get("/api/stats/workflows/timeseries", params={"bucket_hours": 0})
    assert resp.status_code == 422


def test_get_timeseries_bucket_hours_too_large_rejected(client) -> None:
    resp = client.get("/api/stats/workflows/timeseries", params={"bucket_hours": 169})
    assert resp.status_code == 422


def test_post_collect_returns_collector_result(client, stats_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import routers.workflow_stats as ws_router

    async def fake_collect_once(org: str) -> dict:
        assert org  # ORG is passed through
        return {"scanned_repos": 3, "new_runs": 7}

    monkeypatch.setattr(ws_router.workflow_stats, "collect_once", fake_collect_once)
    resp = client.post("/api/stats/workflows/collect")
    assert resp.status_code == 200
    assert resp.json() == {"scanned_repos": 3, "new_runs": 7}


def test_get_summary_requires_auth_when_hub_token_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """``require_fleet_peer`` (#922): once HUB_FLEET_TOKEN is set, an unauthenticated
    caller with no matching bearer token is rejected — mirrors runs_workflows.py's GET
    routes, which use the same dependency."""
    monkeypatch.setenv("HUB_FLEET_TOKEN", "s3cret")  # pragma: allowlist secret
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.workflow_stats import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/stats/workflows")
    assert resp.status_code == 401


def test_post_collect_requires_auth() -> None:
    """Without workflows.control scope, POST /api/stats/workflows/collect is rejected."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.workflow_stats import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/api/stats/workflows/collect")
    assert resp.status_code == 401
