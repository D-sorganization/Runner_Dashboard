"""Prometheus instrumentation for the runner dashboard.

Exposes metrics via a /metrics endpoint (Prometheus text exposition format).
Provides:
  - HTTP request counter and duration histogram (via middleware)
  - GH API call counter and duration histogram (via decorator)
  - Active runner lease gauge
  - Cache size gauges
  - Uptime gauge
  - Active WebSocket connection gauge

Wired into the FastAPI app in server.py (issue #330).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

router = APIRouter(tags=["observability"])

# ─── Counters ─────────────────────────────────────────────────────────────────

HTTP_REQUESTS_TOTAL = Counter(
    "dashboard_http_requests_total",
    "Total HTTP requests received",
    ["method", "path", "status"],
)

GH_API_CALLS_TOTAL = Counter(
    "dashboard_gh_api_calls_total",
    "Total GitHub API calls made",
    ["result"],  # success, 4xx, 5xx, rate_limited
)

DISPATCH_ENVELOPES_TOTAL = Counter(
    "dashboard_dispatch_envelopes_total",
    "Total workflow dispatch envelopes processed",
    ["action", "result"],
)

SUBPROCESS_SPAWNS_TOTAL = Counter(
    "dashboard_subprocess_spawns_total",
    "Total subprocess invocations",
    ["cmd"],
)

REPLAY_DEDUP_HITS_TOTAL = Counter(
    "dashboard_replay_dedup_hits_total",
    "Total replay deduplication cache hits",
)

# ─── Histograms ───────────────────────────────────────────────────────────────

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "dashboard_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

GH_API_DURATION_SECONDS = Histogram(
    "dashboard_gh_api_duration_seconds",
    "GitHub API call duration in seconds",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

SUBPROCESS_DURATION_SECONDS = Histogram(
    "dashboard_subprocess_duration_seconds",
    "Subprocess execution duration in seconds",
    ["cmd"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 15.0, 30.0, 60.0),
)

# ─── Gauges ───────────────────────────────────────────────────────────────────

ACTIVE_LEASES = Gauge(
    "dashboard_active_leases",
    "Number of active runner leases",
    ["principal"],
)

CACHE_SIZE = Gauge(
    "dashboard_cache_size",
    "Current number of entries in the named cache",
    ["cache_name"],
)

RUNNER_CAPACITY = Gauge(
    "dashboard_runner_capacity",
    "Runner capacity by state",
    ["state"],
)

GH_API_RATE_LIMIT_REMAINING = Gauge(
    "dashboard_gh_api_rate_limit_remaining",
    "GitHub API rate limit remaining requests",
)

UPTIME_SECONDS = Gauge(
    "dashboard_uptime_seconds",
    "Seconds since the dashboard process started",
)

ACTIVE_WEBSOCKET_CONNECTIONS = Gauge(
    "dashboard_active_websocket_connections",
    "Number of currently open WebSocket connections",
)

# Module-level start time for uptime tracking.
_PROCESS_START: float = time.time()


def set_process_start(start_time: float) -> None:
    """Override the process start time (called from server.py with BOOT_TIME)."""
    global _PROCESS_START  # noqa: PLW0603
    _PROCESS_START = start_time


def update_uptime() -> None:
    """Refresh the uptime gauge. Called from the Prometheus middleware."""
    UPTIME_SECONDS.set(time.time() - _PROCESS_START)


# ─── Middleware helper ─────────────────────────────────────────────────────────


async def prometheus_middleware(request: Any, call_next: Callable) -> Any:  # type: ignore[misc]
    """ASGI middleware: record HTTP request count and duration."""
    path = request.url.path
    method = request.method
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start

    # Normalise path labels: strip query params (already gone), cap length.
    label_path = path if len(path) <= 120 else path[:120]

    HTTP_REQUESTS_TOTAL.labels(method=method, path=label_path, status=str(response.status_code)).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=label_path).observe(elapsed)
    update_uptime()

    return response


# ─── GH API decorator ─────────────────────────────────────────────────────────


def observe_gh_api_call(result: str, duration_seconds: float) -> None:
    """Record a single GH API call.  Call from gh_utils.py instrumentation points.

    Args:
        result: one of "success", "4xx", "5xx", "rate_limited"
        duration_seconds: wall-clock seconds for the call
    """
    GH_API_CALLS_TOTAL.labels(result=result).inc()
    GH_API_DURATION_SECONDS.observe(duration_seconds)


# ─── /metrics endpoint ────────────────────────────────────────────────────────


@router.get("/metrics", include_in_schema=False)
def metrics_endpoint() -> Response:
    """Prometheus text exposition endpoint. Mounted outside the auth gate."""
    update_uptime()
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
