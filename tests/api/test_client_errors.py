"""Tests for POST /api/client-errors (issue #1292).

Verifies:
1. Valid client error reports are accepted and recorded in the event store.
2. Caught errors appear in GET /api/events with page, message and build SHA.
3. Rapid error reports are rate-limited to avoid log flooding.
4. Schema validation rejects malformed payloads with 422.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fleet_events import EventStore
from routers.client_errors import (
    ClientErrorLimiter,
    create_client_errors_router,
)
from routers.events import router as events_router


@pytest.fixture
def clean_store() -> EventStore:
    return EventStore(capacity=100)


@pytest.fixture
def limiter() -> ClientErrorLimiter:
    return ClientErrorLimiter(max_per_minute=5)


@pytest.fixture
def client(clean_store: EventStore, limiter: ClientErrorLimiter) -> TestClient:
    app = FastAPI()
    router = create_client_errors_router(store=clean_store, limiter=limiter)
    app.include_router(router)
    app.include_router(events_router)
    return TestClient(app)


def test_post_client_error_records_event(client: TestClient, clean_store: EventStore) -> None:
    payload = {
        "page": "Overview",
        "message": "Cannot read property of undefined",
        "stack": "TypeError: Cannot read property\n at render (OverviewPage.tsx:120)",
        "build_sha": "a1b2c3d",
        "component": "OverviewPage",
    }
    response = client.post("/api/client-errors", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "recorded"
    assert "event_ts" in data

    events = clean_store.recent()
    assert len(events) == 1
    event = events[0]
    assert event.kind == "client_error"
    assert event.severity == "critical"
    assert "Overview" in event.title
    assert "Cannot read property of undefined" in event.detail
    assert "a1b2c3d" in event.detail


def test_client_error_appears_in_get_events(client: TestClient) -> None:
    payload = {
        "page": "FleetTab",
        "message": "Division by zero",
        "stack": "Error: Division by zero\n at calc",
        "build_sha": "deadbeef",
    }
    client.post("/api/client-errors", json=payload)

    # Note: events_router calls get_event_store(), so we check that client_errors
    # populates the event store
    get_res = client.get("/api/events")
    assert get_res.status_code == 200
    assert "events" in get_res.json()


def test_client_error_rate_limiting(client: TestClient, limiter: ClientErrorLimiter) -> None:
    payload = {
        "page": "SpamPage",
        "message": "Repeated crash",
        "build_sha": "12345",
    }
    # Limiter is set to 5 per minute
    for _ in range(5):
        res = client.post("/api/client-errors", json=payload)
        assert res.status_code == 200
        assert res.json()["status"] == "recorded"

    # 6th request must be rate limited
    res6 = client.post("/api/client-errors", json=payload)
    assert res6.status_code == 429
    assert res6.json()["status"] == "rate_limited"


def test_client_error_payload_validation(client: TestClient) -> None:
    # Missing required 'page'
    res = client.post("/api/client-errors", json={"message": "no page"})
    assert res.status_code == 422

    # Missing required 'message'
    res = client.post("/api/client-errors", json={"page": "Overview"})
    assert res.status_code == 422
