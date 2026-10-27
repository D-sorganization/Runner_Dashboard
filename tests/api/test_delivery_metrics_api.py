"""GET /api/v1/metrics/* (#2121)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest
from delivery_metrics.db import init_db
from delivery_metrics.parse import parse_check_runs, parse_pr_snapshot
from delivery_metrics.sync import collect_delivery_metrics_once
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import Principal, require_scope
from routers import delivery_metrics as dm_routes

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "delivery_metrics"
REPO = "D-sorganization/Runner_Dashboard"
READER = Principal(id="r", type="user", name="Reader", roles=["viewer"], scopes=["fleet.read"])
_XHR = {"X-Requested-With": "XMLHttpRequest"}


class _FixtureCollector:
    def __init__(self, pr_payload: dict, checks_payload: dict) -> None:
        self._pr = parse_pr_snapshot(REPO, pr_payload)
        head = checks_payload["check_runs"][0]["head_sha"]
        self._runs = parse_check_runs(REPO, head, checks_payload)

    async def list_pulls_updated_since(self, repo_full: str, since: datetime, *, max_pages: int = 3):
        if repo_full != REPO or self._pr.updated_at < since:
            return ()
        return (self._pr,)

    async def fetch_pull(self, repo_full: str, number: int):
        return self._pr

    async def list_check_runs(self, repo_full: str, head_sha: str):
        return self._runs


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    db_path = init_db(tmp_path / "metrics.db")
    pr = json.loads((FIXTURES / "pr_2111.json").read_text(encoding="utf-8"))
    checks = json.loads((FIXTURES / "check_runs_pr_2111.json").read_text(encoding="utf-8"))
    asyncio.run(collect_delivery_metrics_once(_FixtureCollector(pr, checks), [REPO], db_path=db_path))
    dm_routes.set_db_path(db_path)
    monkeypatch.setenv("DASHBOARD_ENABLE_DELIVERY_METRICS", "true")
    application = FastAPI()
    application.include_router(dm_routes.router)
    application.dependency_overrides[require_scope("fleet.read")] = lambda: READER
    return application


def test_summary_404_when_flag_off(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_ENABLE_DELIVERY_METRICS", raising=False)
    client = TestClient(app)
    assert client.get("/api/v1/metrics/summary", headers=_XHR).status_code == 404


def test_weekly_summary(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.get(
        "/api/v1/metrics/weekly-summary",
        params={"since": "2026-10-01T00:00:00Z", "until": "2026-10-31T00:00:00Z"},
        headers=_XHR,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["delivery"]["summary"]["prs"] >= 1
    assert "staff" in body
    assert "markdown" in body


def test_summary_and_pull_drill_down(app: FastAPI) -> None:
    client = TestClient(app)
    summary = client.get(
        "/api/v1/metrics/summary",
        params={"since": "2026-10-01T00:00:00Z", "until": "2026-10-31T00:00:00Z", "repo_full": REPO},
        headers=_XHR,
    )
    assert summary.status_code == 200
    body = summary.json()
    assert body["summary"]["prs"] >= 1
    assert body["groupings"]["by_role"]
    assert body["pr_drill_down"]
    trends = client.get("/api/v1/metrics/trends", params={"repo_full": REPO}, headers=_XHR)
    assert trends.status_code == 200
    assert trends.json()["weekly_trends"]
    pull = client.get("/api/v1/metrics/pull", params={"repo_full": REPO, "number": 2111}, headers=_XHR)
    assert pull.status_code == 200
    assert pull.json()["check_runs"]
