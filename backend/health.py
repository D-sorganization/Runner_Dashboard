"""Health check endpoints for the runner dashboard.

Extracted from server.py as part of issue #159 god-module-refactor-2026q2.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


async def _health_impl() -> dict:
    """Core health logic, callable both from the HTTP endpoint and internally."""
    # Lazy import to avoid circular dependency with server.py
    from server import (  # noqa: PLC0415
        BOOT_TIME,
        HOSTNAME,
        ORG,
        UTC,
        _cache_get,
        _cache_set,
        _deployment_info,
        datetime,
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
    except Exception:  # noqa: BLE001
        gh_ok = False
        runner_count = 0

    return {
        "status": "healthy" if gh_ok else "degraded",
        "timestamp": datetime.now(UTC).isoformat(),
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


@router.get("/health", tags=["diagnostics"])
async def launcher_health_check() -> dict:
    """Minimal health check for launcher recovery detection.

    Returns 200 if backend is ready. Used by PWA recovery modal for
    polling before triggering custom URL protocol.
    """
    # Lazy import to avoid circular dependency with server.py
    from server import UTC, datetime  # noqa: PLC0415

    return {
        "status": "ready",
        "timestamp": datetime.now(UTC).isoformat(),
    }
