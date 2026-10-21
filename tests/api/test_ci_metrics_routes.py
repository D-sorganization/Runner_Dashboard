"""Job metrics telemetry and explicit ingestion retain the existing auth boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def metrics_app(monkeypatch: pytest.MonkeyPatch):
    import routers.workflow_stats as routes
    from identity import Principal, require_fleet_peer, require_scope

    calls: list[tuple] = []
    sample = {"days": 14, "group_by": "repo", "rows": [], "coverage": {"partial": True}}

    def summary(days: int, group_by: str) -> dict:
        calls.append(("query", days, group_by))
        return {**sample, "days": days, "group_by": group_by}

    async def collect(org: str) -> dict:
        calls.append(("collect", org))
        return {"jobs_written": 2, "partial": True, "reason": "request_budget"}

    monkeypatch.setattr(routes, "job_metrics", SimpleNamespace(get_job_metrics=summary), raising=False)
    monkeypatch.setattr(routes, "job_metrics_collector", SimpleNamespace(collect_job_metrics=collect), raising=False)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_fleet_peer] = lambda: "fixture-peer"
    app.dependency_overrides[require_scope("workflows.control")] = lambda: Principal(
        id="fixture-operator", type="bot", name="Fixture Operator", roles=["admin"]
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, calls


def test_metrics_get_is_read_only_and_preserves_coverage(metrics_app) -> None:
    client, calls = metrics_app
    response = client.get("/api/ci/metrics")
    assert response.status_code == 200
    assert response.json()["coverage"] == {"partial": True}
    assert calls == [("query", 14, "repo")]


@pytest.mark.parametrize("dimension", ["repo", "event", "label", "host"])
def test_metrics_supports_each_dimension(metrics_app, dimension: str) -> None:
    client, calls = metrics_app
    response = client.get("/api/ci/metrics", params={"days": 7, "group_by": dimension})
    assert response.status_code == 200
    assert calls == [("query", 7, dimension)]


@pytest.mark.parametrize("params", [{"days": 0}, {"days": 91}, {"group_by": "runner_id"}])
def test_invalid_metrics_query_never_touches_store(metrics_app, params: dict) -> None:
    client, calls = metrics_app
    assert client.get("/api/ci/metrics", params=params).status_code == 422
    assert calls == []


def test_collection_is_explicit_and_surfaces_partial_result(metrics_app) -> None:
    client, calls = metrics_app
    response = client.post("/api/ci/metrics/collect")
    assert response.status_code == 200
    assert response.json() == {"jobs_written": 2, "partial": True, "reason": "request_budget"}
    assert len(calls) == 1
    assert calls[0][0] == "collect"
    assert calls[0][1]


def test_metrics_get_requires_fleet_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    from routers.workflow_stats import router

    monkeypatch.setenv("HUB_FLEET_TOKEN", "fixture-peer-token")
    app = FastAPI()
    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/ci/metrics").status_code == 401


def test_metrics_collection_requires_control_scope() -> None:
    from routers.workflow_stats import router

    app = FastAPI()
    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/api/ci/metrics/collect").status_code == 401
