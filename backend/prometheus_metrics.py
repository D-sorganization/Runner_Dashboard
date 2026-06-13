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
- ``dashboard_health_checks_total``           – Dashboard health outcomes
- ``dashboard_health_check_duration_seconds`` – Dashboard health-check latency
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

    # Health endpoint
    DASHBOARD_HEALTH_CHECKS_TOTAL = Counter(
        "dashboard_health_checks_total",
        "Total dashboard health checks by dashboard and GitHub API status",
        ["status", "github_api"],
    )
    DASHBOARD_HEALTH_DURATION = Histogram(
        "dashboard_health_check_duration_seconds",
        "Dashboard health-check latency in seconds",
        ["status", "github_api"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    )
    DASHBOARD_HUB_CIRCUIT_OPEN = Gauge(
        "dashboard_hub_circuit_open",
        "Whether this node is serving local fleet data because the hub circuit is open",
    )

    # Lease reaper (issue #708)
    LEASE_REAPER_PRUNED_TOTAL = Counter(
        "dashboard_lease_reaper_pruned_total",
        "Total expired runner leases pruned by the background reaper",
    )
    LEASE_ACTIVE_TOTAL = Gauge(
        "dashboard_lease_active_total",
        "Number of active (non-expired) runner leases",
    )

    # Autoscaler (issue #710)
    AUTOSCALER_SCALING_ACTIONS_TOTAL = Counter(
        "autoscaler_scaling_actions_total",
        "Autoscaler scaling actions by action and reason",
        ["action", "reason"],
    )
    AUTOSCALER_BUSY_RUNNERS = Gauge(
        "autoscaler_busy_runners",
        "Number of runners currently busy",
    )
    AUTOSCALER_ACTIVE_RUNNERS = Gauge(
        "autoscaler_active_runners",
        "Number of runners currently active (started)",
    )
    AUTOSCALER_LOAD_RATIO = Gauge(
        "autoscaler_load_ratio",
        "Load average normalized by CPU core count",
    )
    AUTOSCALER_BUSY_CHECK_DURATION = Histogram(
        "autoscaler_busy_check_duration_seconds",
        "Time to run each busy-detection strategy",
        ["strategy"],
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
    )
    AUTOSCALER_DECISION_LOOP_DURATION = Histogram(
        "autoscaler_decision_loop_duration_seconds",
        "Time for one autoscaler decision loop iteration",
        buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    )
    AUTOSCALER_SYSTEMD_ERRORS_TOTAL = Counter(
        "autoscaler_systemd_action_errors_total",
        "Systemd action errors in the autoscaler",
        ["action", "unit"],
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
    DASHBOARD_HEALTH_CHECKS_TOTAL = _stub  # type: ignore[assignment]
    DASHBOARD_HEALTH_DURATION = _stub  # type: ignore[assignment]
    DASHBOARD_HUB_CIRCUIT_OPEN = _stub  # type: ignore[assignment]
    LEASE_REAPER_PRUNED_TOTAL = _stub  # type: ignore[assignment]
    LEASE_ACTIVE_TOTAL = _stub  # type: ignore[assignment]
    AUTOSCALER_SCALING_ACTIONS_TOTAL = _stub  # type: ignore[assignment]
    AUTOSCALER_BUSY_RUNNERS = _stub  # type: ignore[assignment]
    AUTOSCALER_ACTIVE_RUNNERS = _stub  # type: ignore[assignment]
    AUTOSCALER_LOAD_RATIO = _stub  # type: ignore[assignment]
    AUTOSCALER_BUSY_CHECK_DURATION = _stub  # type: ignore[assignment]
    AUTOSCALER_DECISION_LOOP_DURATION = _stub  # type: ignore[assignment]
    AUTOSCALER_SYSTEMD_ERRORS_TOTAL = _stub  # type: ignore[assignment]


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


def record_dashboard_health(status: str, github_api: str, duration_s: float) -> None:
    """Record a completed dashboard health check."""
    DASHBOARD_HEALTH_CHECKS_TOTAL.labels(status=status, github_api=github_api).inc()
    DASHBOARD_HEALTH_DURATION.labels(status=status, github_api=github_api).observe(duration_s)


def set_hub_circuit_open(is_open: bool) -> None:
    """Update the hub-circuit-open gauge."""
    DASHBOARD_HUB_CIRCUIT_OPEN.set(1.0 if is_open else 0.0)


def update_lease_gauge(active_count: int) -> None:
    """Update the active runner leases gauge."""
    RUNNER_LEASES_ACTIVE.set(active_count)


def record_lease_expired(count: int = 1) -> None:
    """Record expired runner leases."""
    RUNNER_LEASES_EXPIRED_TOTAL.inc(count)


def record_autoscaler_action(action: str, reason: str) -> None:
    """Record an autoscaler scaling action (issue #710).

    Pre-condition: action must be 'start' or 'stop'.
    Pre-condition: reason must be one of 'busy', 'idle', 'load', 'manual'.
    """
    assert action in ("start", "stop"), f"Invalid action: {action}"
    assert reason in ("busy", "idle", "load", "manual"), f"Invalid reason: {reason}"
    AUTOSCALER_SCALING_ACTIONS_TOTAL.labels(action=action, reason=reason).inc()


def record_autoscaler_systemd_error(action: str, unit: str) -> None:
    """Record a systemd error in the autoscaler (issue #710).

    Pre-condition: action and unit must be non-empty strings.
    """
    assert action, "action must be non-empty"
    assert unit, "unit must be non-empty"
    AUTOSCALER_SYSTEMD_ERRORS_TOTAL.labels(action=action, unit=unit).inc()


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
