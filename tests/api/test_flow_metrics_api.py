from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import require_fleet_peer
from routers import flow_metrics as flow_metrics_router


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    async def fake_collect(*, repo: str | None = None, run_gh=None, now=None):  # noqa: ANN001
        return {
            "available": True,
            "complete": True,
            "pr_age_rule_hours": 72.0,
            "generated_at": datetime(2026, 11, 2, tzinfo=UTC).isoformat().replace("+00:00", "Z"),
            "repos": [{"repo": repo or "Runner_Dashboard", "open_pr_age": {"count": 0}}],
        }

    monkeypatch.setattr(flow_metrics_router, "collect_flow_metrics", fake_collect)
    app = FastAPI()
    app.include_router(flow_metrics_router.router)
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.unit
def test_flow_metrics_route_returns_payload(client: TestClient) -> None:
    resp = client.get("/api/v1/fleet/flow-metrics?repo=Runner_Dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["repos"][0]["repo"] == "Runner_Dashboard"
