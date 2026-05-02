"""Prometheus instrumentation for the runner dashboard (issue #330).

Exposes a ``/metrics`` endpoint in the Prometheus text exposition format using
the ``prometheus_client`` library.  Metrics collected:

- ``dashboard_http_requests_total``          – HTTP request counts by method/path/status
- ``dashboard_http_request_duration_seconds`` – HTTP request latency histogram
- ``dashboard_github_api_calls_total``        – GitHub API call counts by method/endpoint
- ``dashboard_github_api_duration_seconds``   – GitHub API call latency histogram
- ``dashboard_runner_leases_active``          – Active runner leases gauge
- ``dashboard_runner_leases_expired_total``   – Expired runner leases counter
- ``dashboard_cache_hits_total``              – Cache hits by cache name
- ``dashboard_cache_misses_total``            – Cache misses by cache name
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Response

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover — missing in minimal test envs
    _PROMETHEUS_AVAILABLE = False

router = APIRouter(tags=["observability"])

# ─── Metric definitions ────────────────────────────────────────────────────────

if _PROMETHEUS_AVAILABLE:
    # HTTP layer
    HTTP_REQUESTS_TOTAL = Counter(
        "dashboard_http_requests_total",
        "Total HTTP requests processed by the dashboard",
        ["method", "endpoint", "status_code"],
    )
    HTTP_REQUEST_DURATION = Histogram(
        "dashboard_http_request_duration_seconds",
        "HTTP request latency in seconds",
        ["method", "endpoint"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    )

    # GitHub API layer
    GH_API_CALLS_TOTAL = Counter(
        "dashboard_github_api_calls_total",
        "Total GitHub API calls made by the dashboard",
        ["method", "endpoint"],
    )
    GH_API_DURATION = Histogram(
        "dashboard_github_api_duration_seconds",
        "GitHub API call latency in seconds",
        ["method", "endpoint"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    )

    # Runner leases
    RUNNER_LEASES_ACTIVE = Gauge(
        "dashboard_runner_leases_active",
        "Number of currently active runner leases",
    )
    RUNNER_LEASES_EXPIRED_TOTAL = Counter(
        "dashboard_runner_leases_expired_total",
        "Total runner leases that have expired",
    )

    # Cache
    CACHE_HITS_TOTAL = Counter(
        "dashboard_cache_hits_total",
        "Total cache hits",
        ["cache"],
    )
    CACHE_MISSES_TOTAL = Counter(
        "dashboard_cache_misses_total",
        "Total cache misses",
        ["cache"],
    )
else:  # pragma: no cover
    # Stub objects so imports don't fail when prometheus_client is absent
    class _Stub:  # type: ignore[override]
        def labels(self, **_kw: Any) -> _Stub:
            return self

        def inc(self, _amount: float = 1) -> None:
            pass

        def observe(self, _amount: float) -> None:
            pass

        def set(self, _value: float) -> None:  # noqa: A003
            pass

        def time(self) -> Any:
            import contextlib

            return contextlib.nullcontext()

    _stub = _Stub()
    HTTP_REQUESTS_TOTAL = _stub  # type: ignore[assignment]
    HTTP_REQUEST_DURATION = _stub  # type: ignore[assignment]
    GH_API_CALLS_TOTAL = _stub  # type: ignore[assignment]
    GH_API_DURATION = _stub  # type: ignore[assignment]
    RUNNER_LEASES_ACTIVE = _stub  # type: ignore[assignment]
    RUNNER_LEASES_EXPIRED_TOTAL = _stub  # type: ignore[assignment]
    CACHE_HITS_TOTAL = _stub  # type: ignore[assignment]
    CACHE_MISSES_TOTAL = _stub  # type: ignore[assignment]


# ─── Helpers for external callers ─────────────────────────────────────────────


def record_gh_api_call(method: str, endpoint: str, duration_s: float) -> None:
    """Record a completed GitHub API call (call from gh_utils or http_clients)."""
    GH_API_CALLS_TOTAL.labels(method=method.upper(), endpoint=endpoint).inc()
    GH_API_DURATION.labels(method=method.upper(), endpoint=endpoint).observe(duration_s)


def record_cache_hit(cache_name: str) -> None:
    """Record a cache hit for the named cache."""
    CACHE_HITS_TOTAL.labels(cache=cache_name).inc()


def record_cache_miss(cache_name: str) -> None:
    """Record a cache miss for the named cache."""
    CACHE_MISSES_TOTAL.labels(cache=cache_name).inc()


def update_lease_gauge(active_count: int) -> None:
    """Update the active runner leases gauge."""
    RUNNER_LEASES_ACTIVE.set(active_count)


def record_lease_expired(count: int = 1) -> None:
    """Record expired runner leases."""
    RUNNER_LEASES_EXPIRED_TOTAL.inc(count)


# ─── ASGI middleware ──────────────────────────────────────────────────────────


class PrometheusMiddleware:
    """ASGI middleware that records HTTP request counts and latencies.

    Attaches to the application in server.py via ``app.middleware("http")``.
    Route paths are normalised so path-parameter variants (``/api/runs/123``)
    are bucketed under the parameterised pattern (``/api/runs/{run_id}``) when
    the matched route template is available.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not _PROMETHEUS_AVAILABLE:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "UNKNOWN")
        status_code = 500

        start = time.perf_counter()

        async def send_wrapper(message: Any) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            # Prefer the route template (e.g. /api/runs/{run_id}) over the raw path
            route = scope.get("route")
            endpoint = getattr(route, "path", path) if route else path
            HTTP_REQUESTS_TOTAL.labels(
                method=method,
                endpoint=endpoint,
                status_code=str(status_code),
            ).inc()
            HTTP_REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(duration)


# ─── /metrics endpoint ────────────────────────────────────────────────────────


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    """Expose Prometheus metrics in the text exposition format (issue #330).

    The endpoint is intentionally unauthenticated so that Prometheus scrapers
    running without dashboard credentials can reach it.  Sensitive runtime
    values (API keys, tokens, session data) are never included in metrics.
    """
    if not _PROMETHEUS_AVAILABLE:
        return Response(
            content="# prometheus_client not installed\n",
            media_type="text/plain; version=0.0.4",
            status_code=503,
        )

    # Refresh lease gauge before scrape (lazy — avoids a background task)
    _refresh_lease_gauge()

    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


def _refresh_lease_gauge() -> None:
    """Update the active-leases gauge from the live LeaseManager if available."""
    try:
        from runner_lease import LeaseManager  # noqa: PLC0415

        mgr = LeaseManager()
        now = __import__("time").time()
        active = [lz for lz in mgr.leases if lz.expires_at is None or lz.expires_at > now]
        RUNNER_LEASES_ACTIVE.set(len(active))
    except Exception as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        pass  # Non-fatal: gauge just won't update this scrape
