"""Tests for backend/instrumentation.py — issue #386.

Covers the public API of the Prometheus instrumentation module:
  - set_process_start / update_uptime
  - observe_gh_api_call
  - prometheus_middleware (async ASGI middleware helper)
  - metrics_endpoint (/metrics route)

All Prometheus metric writes are side-effectful but idempotent for testing
purposes; we just assert the functions complete without error and that the
metrics endpoint returns valid Prometheus text format.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import instrumentation as inst
import pytest

# ---------------------------------------------------------------------------
# set_process_start / update_uptime
# ---------------------------------------------------------------------------


def test_set_process_start_updates_module_var() -> None:
    original = inst._PROCESS_START
    try:
        fixed_time = 1_000_000.0
        inst.set_process_start(fixed_time)
        assert inst._PROCESS_START == pytest.approx(fixed_time)
    finally:
        inst._PROCESS_START = original


def test_update_uptime_does_not_raise() -> None:
    """update_uptime() must not raise even when called multiple times."""
    inst.update_uptime()
    inst.update_uptime()


def test_update_uptime_after_set_process_start() -> None:
    """Uptime must be non-negative after setting a past start time."""
    original = inst._PROCESS_START
    try:
        past = time.time() - 3600  # 1 hour ago
        inst.set_process_start(past)
        # Should not raise; UPTIME_SECONDS gauge is set to ~3600
        inst.update_uptime()
    finally:
        inst._PROCESS_START = original


# ---------------------------------------------------------------------------
# observe_gh_api_call
# ---------------------------------------------------------------------------


def test_observe_gh_api_call_success() -> None:
    """observe_gh_api_call must not raise for 'success' result."""
    inst.observe_gh_api_call("success", 0.123)


def test_observe_gh_api_call_4xx() -> None:
    inst.observe_gh_api_call("4xx", 0.05)


def test_observe_gh_api_call_5xx() -> None:
    inst.observe_gh_api_call("5xx", 1.5)


def test_observe_gh_api_call_rate_limited() -> None:
    inst.observe_gh_api_call("rate_limited", 2.7)


# ---------------------------------------------------------------------------
# prometheus_middleware (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prometheus_middleware_happy_path() -> None:
    """Middleware records metrics and returns the response from call_next."""
    mock_response = MagicMock()
    mock_response.status_code = 200

    async def call_next(req: object) -> MagicMock:
        return mock_response

    request = MagicMock()
    request.url.path = "/api/runners"
    request.method = "GET"

    response = await inst.prometheus_middleware(request, call_next)
    assert response is mock_response


@pytest.mark.asyncio
async def test_prometheus_middleware_post_request() -> None:
    """Middleware works for POST requests too."""
    mock_response = MagicMock()
    mock_response.status_code = 201

    async def call_next(req: object) -> MagicMock:
        return mock_response

    request = MagicMock()
    request.url.path = "/api/dispatch"
    request.method = "POST"

    response = await inst.prometheus_middleware(request, call_next)
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_prometheus_middleware_long_path_truncated() -> None:
    """Paths longer than 120 chars are truncated for the label."""
    mock_response = MagicMock()
    mock_response.status_code = 404

    async def call_next(req: object) -> MagicMock:
        return mock_response

    request = MagicMock()
    request.url.path = "/api/" + "x" * 200  # >120 chars
    request.method = "GET"

    # Should not raise; label is truncated internally
    response = await inst.prometheus_middleware(request, call_next)
    assert response is mock_response


@pytest.mark.asyncio
async def test_prometheus_middleware_500_response() -> None:
    """Middleware records 500 status without raising."""
    mock_response = MagicMock()
    mock_response.status_code = 500

    async def call_next(req: object) -> MagicMock:
        return mock_response

    request = MagicMock()
    request.url.path = "/api/broken"
    request.method = "GET"

    response = await inst.prometheus_middleware(request, call_next)
    assert response.status_code == 500


# ---------------------------------------------------------------------------
# metrics_endpoint
# ---------------------------------------------------------------------------


def test_metrics_endpoint_returns_response() -> None:
    """metrics_endpoint() must return a Response with Prometheus content type."""
    from fastapi import Response

    resp = inst.metrics_endpoint()
    assert isinstance(resp, Response)
    assert "text/plain" in resp.media_type


def test_metrics_endpoint_body_is_bytes() -> None:
    """The response body must be bytes (Prometheus text format)."""
    resp = inst.metrics_endpoint()
    assert isinstance(resp.body, bytes)


def test_metrics_endpoint_contains_dashboard_metrics() -> None:
    """The response body should contain at least one dashboard metric name."""
    resp = inst.metrics_endpoint()
    body = resp.body.decode("utf-8")
    # At minimum the uptime metric should be present
    assert "dashboard_uptime_seconds" in body


# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------


def test_router_is_api_router() -> None:
    """The module must expose an APIRouter instance."""
    from fastapi import APIRouter

    assert isinstance(inst.router, APIRouter)


def test_metrics_route_registered() -> None:
    """The /metrics route must be registered on the router."""
    paths = [r.path for r in inst.router.routes]  # type: ignore[attr-defined]
    assert "/metrics" in paths
