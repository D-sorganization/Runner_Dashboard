"""Statistics and usage monitoring routes."""

import logging

from fastapi import APIRouter

log = logging.getLogger("dashboard")
router = APIRouter()


@router.get("/api/stats")
async def get_stats() -> dict:
    """Return real-time statistics."""
    return {"stats": {}}


@router.get("/api/usage")
async def get_usage() -> dict:
    """Return runner usage metrics."""
    return {"usage": {}}


@router.get("/api/local-apps")
async def get_local_apps() -> dict:
    """List local applications and their statuses."""
    return {"apps": []}


@router.get("/api/watchdog")
async def get_watchdog_status() -> dict:
    """Get watchdog process health."""
    return {"status": "healthy"}
