"""Health check endpoints for the runner dashboard.

Extracted from server.py as part of issue #159 god-module-refactor-2026q2.

Issue #332 — split /livez (no I/O) from /readyz (dependency checks).

/livez  — liveness probe. Returns 200 unconditionally if the process is up.
          No I/O, no dependency checks.  Suitable for systemd WatchdogSec /
          Kubernetes liveness probe.

/readyz — readiness probe. Runs a set of lightweight dependency probes
          (GH_TOKEN presence, gh CLI in PATH, SQLite stores readable).
          Returns 200 only when all probes pass; 503 otherwise with a
          structured ``{ status, checks }`` body.

/api/health — human-readable composite view (backward compat).  Retains the
              previous behaviour of checking GitHub API reachability and
              returning uptime/runner counts.
"""

from __future__ import annotations

import datetime as _dt_mod
import time

import dashboard_config
from cache_utils import cache_size
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from readiness import aggregate, get_default_probes

router = APIRouter(tags=["health"])

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017


# ---------------------------------------------------------------------------
# /livez — liveness probe (NO I/O, always 200 if process is alive)
# ---------------------------------------------------------------------------


@router.get("/livez", tags=["diagnostics"])
async def livez() -> dict:
    """Liveness probe.

    Returns 200 with ``{"status": "ok"}`` unconditionally as long as the
    Python process is running and able to handle requests.  Performs no I/O
    and checks no external dependencies.

    Use this as the liveness target for systemd ``WatchdogSec`` or a
    Kubernetes liveness probe.  If this endpoint starts failing, the process
    is likely deadlocked or OOM-killed — not merely waiting for a dependency.
    """
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# /readyz — readiness probe (dependency checks, may return 503)
# ---------------------------------------------------------------------------


@router.get("/readyz", tags=["diagnostics"])
async def readyz() -> JSONResponse:
    """Readiness probe.

    Runs lightweight checks for each required dependency:
    - ``github_token`` — ``GH_TOKEN`` env var is present
    - ``gh_cli``       — ``gh`` binary is in PATH
    - ``lease_db``     — replay/lease SQLite store is readable
    - ``push_db``      — push-subscription SQLite store is readable

    Returns 200 when all checks pass.  Returns 503 with a structured body:
    ``{ "status": "down"|"degraded", "checks": { <name>: <status>|{status, detail} } }``
    when any check fails.

    Also includes ``session_secret_source`` for backward compatibility with
    existing monitoring scripts that key on that field.
    """
    assert dashboard_config.SESSION_SECRET_SOURCE in {
        "env",
        "persisted",
        "generated",
    }, f"unexpected SESSION_SECRET_SOURCE: {dashboard_config.SESSION_SECRET_SOURCE!r}"

    http_status, body = await aggregate(get_default_probes())
    body["session_secret_source"] = dashboard_config.SESSION_SECRET_SOURCE
    body["timestamp"] = _dt_mod.datetime.now(UTC).isoformat()
    return JSONResponse(content=body, status_code=http_status)


# ---------------------------------------------------------------------------
# /api/health — human-readable composite (backward compat, retains I/O)
# ---------------------------------------------------------------------------


async def _health_impl() -> dict:
    """Core health logic, callable both from the HTTP endpoint and internally."""
    # Lazy import to avoid circular dependency with server.py
    from server import (  # noqa: PLC0415
        BOOT_TIME,
        HOSTNAME,
        ORG,
        _cache_get,
        _cache_set,
        _deployment_info,
        gh_api_admin,
    )

    try:
        # Reuse the runner cache so health checks don't add extra API calls.
        data = _cache_get("runners", 25.0)
        if data is None:
            data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
            _cache_set("runners", data)
        gh_ok = True
        runner_count = len(data.get("runners", []))
    except Exception as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        gh_ok = False
        runner_count = 0

    return {
        "status": "healthy" if gh_ok else "degraded",
        "timestamp": _dt_mod.datetime.now(UTC).isoformat(),
        "hostname": HOSTNAME,
        "github_api": "connected" if gh_ok else "unreachable",
        "runners_registered": runner_count,
        "dashboard_uptime_seconds": int(time.time() - BOOT_TIME),
        "deployment": _deployment_info(),
    }


@router.get("/api/health")
async def health_check(request: Request):
    """Health endpoint for monitoring and load balancers."""
    return await _health_impl()


@router.get("/api/version")
async def api_version() -> dict:
    """Return dashboard and dispatch-envelope version compatibilities."""
    from dispatch_contract import (  # noqa: PLC0415
        MAX_ENVELOPE_VERSION,
        MIN_ENVELOPE_VERSION,
        SUPPORTED_SCHEMA_VERSIONS,
    )
    from server import _deployment_info  # noqa: PLC0415

    dep = _deployment_info()
    return {
        "dashboard": dep.get("version", "unknown"),
        "envelope": {
            "min": MIN_ENVELOPE_VERSION,
            "max": MAX_ENVELOPE_VERSION,
            "supported": sorted(list(SUPPORTED_SCHEMA_VERSIONS)),
        },
        "git_sha": dep.get("git_sha", "unknown"),
        "build_time": dep.get("build_time", "unknown"),
    }


@router.get("/health", tags=["diagnostics"])
async def launcher_health_check() -> dict:
    """Minimal health check for launcher recovery detection.

    Returns 200 if backend is ready. Used by PWA recovery modal for
    polling before triggering custom URL protocol.
    """
    return {
        "status": "ready",
        "timestamp": _dt_mod.datetime.now(UTC).isoformat(),
    }


@router.get("/api/cache/size", tags=["diagnostics"])
async def get_cache_size() -> dict:
    """Return current entry count for each bounded cache.

    Exposes the ``dashboard_cache_entries{cache}`` gauge so operators and
    monitoring scripts can track memory growth without a Prometheus scrape.
    Response shape: ``{"gauges": {"dashboard_cache_entries": {<cache>: <count>}}}``.
    """
    sizes = cache_size()
    return {
        "gauges": {
            "dashboard_cache_entries": sizes,
        },
        "max_sizes": {
            "main": dashboard_config.MAX_CACHE_SIZE,
        },
    }
