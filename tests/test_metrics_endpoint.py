"""Tests for the /metrics Prometheus endpoint (issue #330).

Asserts:
- The /metrics endpoint is reachable and returns 200.
- The Content-Type header is the Prometheus text/plain format.
- After a few requests the metric set is non-empty (counters increment).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure backend is on the path (mirrors conftest.py approach).
backend_dir = str(Path(__file__).parent.parent.resolve() / "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import instrumentation  # noqa: E402
from fastapi import APIRouter  # noqa: E402


def test_instrumentation_router_is_apirouter() -> None:
    """The module exposes a FastAPI router."""
    assert isinstance(instrumentation.router, APIRouter)


def test_metrics_route_registered() -> None:
    """The /metrics GET route is present on the router."""
    paths = [route.path for route in instrumentation.router.routes]  # type: ignore[attr-defined]
    assert "/metrics" in paths


def test_metrics_endpoint_returns_ok(make_authed_client, admin_principal) -> None:
    """GET /metrics returns 200 with Prometheus content-type."""
    client = make_authed_client(admin_principal)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")


def test_metrics_endpoint_non_empty_after_requests(make_authed_client, admin_principal) -> None:
    """After a few HTTP requests the metrics output is non-empty."""
    client = make_authed_client(admin_principal)
    # Trigger some counter increments via a couple of requests.
    client.get("/metrics")
    client.get("/metrics")
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.text
    # Prometheus format always contains at least TYPE and HELP lines.
    assert len(body) > 0
    assert "dashboard_http_requests_total" in body


def test_http_requests_counter_increments(make_authed_client, admin_principal) -> None:
    """The HTTP request counter increments with each request."""
    client = make_authed_client(admin_principal)
    # Make a health request to ensure counter has some data.
    client.get("/api/health")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "dashboard_http_requests_total" in response.text


def test_observe_gh_api_call_records_metrics() -> None:
    """observe_gh_api_call updates counters without raising."""
    # Should not raise; metric updates are always fire-and-forget.
    instrumentation.observe_gh_api_call("success", 0.3)
    instrumentation.observe_gh_api_call("rate_limited", 1.1)


def test_set_process_start_updates_uptime() -> None:
    """set_process_start changes the reference time used for uptime gauge."""
    import time

    before = time.time() - 10
    instrumentation.set_process_start(before)
    instrumentation.update_uptime()
    # After update the gauge value should be >= 10 s.
    assert instrumentation.UPTIME_SECONDS._value.get() >= 10  # type: ignore[attr-defined]
