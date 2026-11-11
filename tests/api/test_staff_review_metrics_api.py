"""API test for the read-only review-flow metrics endpoint (#4250)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity import require_fleet_peer
from routers import staff_review_metrics as router_mod


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    async def fake_collect(*, repo: str | None = None, database=None):  # noqa: ANN001
        return {
            "generated_at": "2026-11-09T00:00:00Z",
            "queue_depth": 50,
            "queue_depth_by_repo": {repo or "Runner_Dashboard": 50},
            "verdict_mix": {"approve": 1, "changes": 0, "escalate": 0},
            "review_runs_by_machine_provider": {"hub-a": {"claude": 60}},
            "provider_availability": [{"provider": "claude", "status": "ok", "detail": ""}],
            "provider_skips": {},
            "rate_limit": {"count_429": 1, "throttled_seconds": 66.0},
            "deferred": {
                "review_latency_p50_p90": "needs #4249",
                "reviewer_disagreement_rate": "needs #4249",
            },
            "summary": {
                "queue_depth": "50 green, non-draft, unapproved PR(s) queued for review.",
                "review_latency_p90": "Deferred: needs the #4249 per-head-SHA verdict contract.",
                "throttled_time": "1 dispatch 429(s), 66s throttled since process start.",
            },
        }

    monkeypatch.setattr(router_mod, "collect_review_metrics", fake_collect)
    monkeypatch.setattr(router_mod, "get_runner", lambda: type("R", (), {"store": type("S", (), {"path": ""})()})())
    app = FastAPI()
    app.include_router(router_mod.router)
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.unit
def test_review_metrics_route_returns_payload(client: TestClient) -> None:
    resp = client.get("/api/v1/staff/review/metrics?repo=Runner_Dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["queue_depth"] == 50
    assert body["verdict_mix"]["approve"] == 1
    assert "review_latency_p50_p90" in body["deferred"]
    assert "queue_depth" in body["summary"]


@pytest.mark.unit
def test_review_metrics_route_rejects_invalid_repo(client: TestClient) -> None:
    resp = client.get("/api/v1/staff/review/metrics?repo=..%2F..")
    assert resp.status_code == 422
