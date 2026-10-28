"""Purge inputs must be bounded before any cancellation is attempted (#3030)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def purge_context(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "backend"))
    monkeypatch.setenv("DASHBOARD_IDENTITY_DIR", str(tmp_path / "identity"))
    monkeypatch.setenv("RUNNER_DASHBOARD_CONFIG_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AUTODERIVE_FLEET_NODES", "0")
    monkeypatch.setenv("SESSION_SECRET", "purge-route-test-session-secret")
    from identity import Principal, require_fleet_peer, require_principal
    from routers import queue

    app = FastAPI()
    app.include_router(queue.router)
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    app.dependency_overrides[require_principal] = lambda: Principal(
        id="test-operator", type="bot", name="Test Operator", roles=["operator"]
    )
    monkeypatch.setattr(queue, "should_proxy_fleet_to_hub", lambda request: False)
    purge = AsyncMock(return_value={"runs": [], "cancelled_count": 0})
    monkeypatch.setattr(queue, "purge_stale_runs", purge)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, purge


@pytest.mark.parametrize(
    "body",
    [
        {"min_age_minutes": 0},
        {"min_age_minutes": 20161},
        {"min_age_minutes": "bad"},
        {"min_age": -1},
        {"max_count": 0},
        {"max_count": 10000},
        {"max_count": "bad"},
        {"max_cancel": 501},
    ],
)
def test_purge_stale_body_bounds(purge_context, body):
    client, purge = purge_context
    response = client.post("/api/queue/purge-stale", json=body)
    assert response.status_code == 422
    purge.assert_not_called()


@pytest.mark.parametrize(
    ("body", "age", "count"),
    [({}, 90, 7), ({"min_age_minutes": 1, "max_count": 500}, 1, 500), ({"min_age": 120, "max_cancel": 3}, 120, 3)],
)
def test_valid_body_overrides_query_after_validation(purge_context, body, age, count):
    client, purge = purge_context
    response = client.post("/api/queue/purge-stale?min_age_minutes=90&max_count=7", json=body)
    assert response.status_code == 200
    assert purge.call_args.kwargs["min_age_minutes"] == age
    assert purge.call_args.kwargs["max_count"] == count
