"""Prometheus instrumentation helpers for the runner dashboard (issue #386).

Public API
----------
- ``_PROCESS_START``          – float timestamp set at import time
- ``set_process_start(t)``    – override ``_PROCESS_START`` (useful in tests)
- ``update_uptime()``         – push current uptime to the gauge
- ``observe_gh_api_call(result, duration_s)`` – record a GH API call by result
- ``prometheus_middleware(request, call_next)`` – async ASGI middleware helper
- ``metrics_endpoint()``      – return a Prometheus-format ``fastapi.Response``
- ``router``                  – ``APIRouter`` with ``/metrics`` registered

This module is the single import point for instrumentation; it owns the
uptime / GH-API-result metrics and delegates HTTP-layer metrics to
``prometheus_metrics``.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
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
except ImportError:  # pragma: no cover — minimal test environments
    _PROMETHEUS_AVAILABLE = False

# ---------------------------------------------------------------------------
# Module-level start time (seconds since epoch, set at import)
# ---------------------------------------------------------------------------

_PROCESS_START: float = time.time()

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

if _PROMETHEUS_AVAILABLE:
    UPTIME_SECONDS: Gauge = Gauge(
        "dashboard_uptime_seconds",
        "Seconds the dashboard process has been running",
    )
    GH_API_CALLS_TOTAL: Counter = Counter(
        "dashboard_instrumentation_gh_api_calls_total",
        "Total GitHub API calls recorded via instrumentation, by result",
        ["result"],
    )
    GH_API_DURATION: Histogram = Histogram(
        "dashboard_instrumentation_gh_api_duration_seconds",
        "GitHub API call latency recorded via instrumentation",
        ["result"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    )
    HTTP_REQUESTS_TOTAL: Counter = Counter(
        "dashboard_instrumentation_http_requests_total",
        "Total HTTP requests observed via instrumentation middleware",
        ["method", "endpoint", "status_code"],
    )
    HTTP_REQUEST_DURATION: Histogram = Histogram(
        "dashboard_instrumentation_http_request_duration_seconds",
        "HTTP request latency observed via instrumentation middleware",
        ["method", "endpoint"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    )
else:  # pragma: no cover — stubs when prometheus_client is absent

    class _Stub:  # type: ignore[override]
        def labels(self, **_kw: Any) -> _Stub:
            return self

        def inc(self, _amount: float = 1) -> None:
            pass

        def observe(self, _amount: float) -> None:
            pass

        def set(self, _value: float) -> None:  # noqa: A003
            pass

    _stub = _Stub()
    UPTIME_SECONDS = _stub  # type: ignore[assignment]
    GH_API_CALLS_TOTAL = _stub  # type: ignore[assignment]
    GH_API_DURATION = _stub  # type: ignore[assignment]
    HTTP_REQUESTS_TOTAL = _stub  # type: ignore[assignment]
    HTTP_REQUEST_DURATION = _stub  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def set_process_start(t: float) -> None:
    """Set the module-level ``_PROCESS_START`` timestamp."""
    global _PROCESS_START
    _PROCESS_START = t


def update_uptime() -> None:
    """Push the current uptime (seconds) into the ``dashboard_uptime_seconds`` gauge."""
    UPTIME_SECONDS.set(time.time() - _PROCESS_START)


def observe_gh_api_call(result: str, duration_s: float) -> None:
    """Record a completed GitHub API call.

    Parameters
    ----------
    result:
        Outcome label — one of ``"success"``, ``"4xx"``, ``"5xx"``,
        ``"rate_limited"``, or any other string.
    duration_s:
        Wall-clock time for the call in seconds.
    """
    GH_API_CALLS_TOTAL.labels(result=result).inc()
    GH_API_DURATION.labels(result=result).observe(duration_s)


# ---------------------------------------------------------------------------
# Async middleware helper
# ---------------------------------------------------------------------------

_MAX_LABEL_LEN = 120


async def prometheus_middleware(
    request: Any,
    call_next: Callable[[Any], Awaitable[Any]],
) -> Any:
    """Async middleware helper that records HTTP request metrics.

    Designed for use inside a ``@app.middleware("http")`` handler:

    .. code-block:: python

        @app.middleware("http")
        async def _instr(request: Request, call_next):
            return await prometheus_middleware(request, call_next)

    Paths longer than ``_MAX_LABEL_LEN`` (120) characters are truncated before
    being used as Prometheus label values to avoid cardinality explosion.
    """
    path: str = request.url.path
    endpoint = path[:_MAX_LABEL_LEN] if len(path) > _MAX_LABEL_LEN else path
    method: str = request.method

    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start

    status_code = str(response.status_code)
    HTTP_REQUESTS_TOTAL.labels(method=method, endpoint=endpoint, status_code=status_code).inc()
    HTTP_REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(duration)

    return response


# ---------------------------------------------------------------------------
# /metrics endpoint
# ---------------------------------------------------------------------------

router = APIRouter(tags=["observability"])


def metrics_endpoint() -> Response:
    """Return Prometheus metrics in the text exposition format.

    This is a plain (non-async) helper so tests can call it directly.  The
    route handler on ``router`` delegates to this function.
    """
    update_uptime()

    if not _PROMETHEUS_AVAILABLE:  # pragma: no cover
        return Response(
            content=b"# prometheus_client not installed\n",
            media_type="text/plain; version=0.0.4",
            status_code=503,
        )

    data: bytes = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


@router.get("/metrics")
async def _metrics_route() -> Response:
    """Expose Prometheus metrics (delegates to ``metrics_endpoint``)."""
    return metrics_endpoint()
