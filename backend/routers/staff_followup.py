"""FastAPI router for Barb Follow-up Engine (SC-C4, Issue #1327).

Endpoints:
- POST /api/v1/staff/followup/sweep: Run an on-demand follow-up sweep
- GET /api/v1/staff/followup/status: Inspect follow-up engine status and watchdog health
- GET /api/v1/staff/followup/digest: Retrieve daily digest of follow-up metrics
"""

# ruff: noqa: B008
from __future__ import annotations

import logging
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    status,
)
from identity import Principal, require_scope
from staff.followup import (
    FollowupEngine,
    get_followup_engine,
)

log = logging.getLogger("dashboard.staff.followup_router")

router = APIRouter(tags=["staff-followup"])


@router.post(
    "/followup/sweep",
    status_code=status.HTTP_200_OK,
)
async def trigger_sweep(
    caller: Principal = Depends(require_scope("staff.dispatch")),
    engine: FollowupEngine = Depends(get_followup_engine),
) -> dict[str, Any]:
    """Execute an on-demand follow-up sweep over active work items and runs."""
    result = engine.sweep()
    return {
        "ok": True,
        **result.to_dict(),
    }


@router.get(
    "/followup/status",
    status_code=status.HTTP_200_OK,
)
async def get_status(
    caller: Principal = Depends(require_scope("staff.read")),
    engine: FollowupEngine = Depends(get_followup_engine),
) -> dict[str, Any]:
    """Check the watchdog status and health of the Barb follow-up engine."""
    watchdog = engine.check_watchdog()
    last_sweep = engine.last_sweep_at.isoformat() if engine.last_sweep_at else None
    return {
        "ok": watchdog.get("ok", True),
        "last_sweep_at": last_sweep,
        "sweep_interval_seconds": engine.sweep_interval_seconds,
        "watchdog": watchdog,
    }


@router.get(
    "/followup/digest",
    status_code=status.HTTP_200_OK,
)
async def get_digest(
    caller: Principal = Depends(require_scope("staff.read")),
    engine: FollowupEngine = Depends(get_followup_engine),
) -> dict[str, Any]:
    """Retrieve the daily digest of follow-up activities and work item states."""
    digest = engine.get_daily_digest()
    return digest.to_dict()
