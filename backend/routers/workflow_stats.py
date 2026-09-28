"""Workflow duration statistics routes (issue #1735).

Covers:
  - GET  /api/stats/workflows              – P50/P95 duration summary per workflow/repo
  - GET  /api/stats/workflows/timeseries   – bucketed duration series for trend charting
  - POST /api/stats/workflows/collect      – trigger an on-demand collection pass

Auth mirrors ``routers/runs_workflows.py``: GET routes use ``require_fleet_peer``
(tailnet/hub-readable telemetry), the POST route uses
``require_scope("workflows.control")`` (same scope as ``POST /api/workflows/dispatch``).
"""

from __future__ import annotations

import asyncio
import logging

import workflow_stats
from dashboard_config import ORG
from fastapi import APIRouter, Depends, Query
from identity import Principal, require_fleet_peer, require_scope

log = logging.getLogger("dashboard.workflow_stats")
router = APIRouter(tags=["workflow_stats"])


@router.get("/api/stats/workflows", dependencies=[Depends(require_fleet_peer)])
async def get_workflow_stats_summary(
    days: int = Query(14, ge=1, le=90),
    group_by: str = "workflow",
) -> dict:
    """Return P50/P95 duration + success-rate stats per workflow (or repo)."""
    await asyncio.to_thread(workflow_stats.init_db)
    return await asyncio.to_thread(workflow_stats.get_summary, days, group_by)


@router.get("/api/stats/workflows/timeseries", dependencies=[Depends(require_fleet_peer)])
async def get_workflow_stats_timeseries(
    days: int = Query(30, ge=1, le=90),
    bucket_hours: int = Query(24, ge=1, le=168),
    repo: str | None = None,
    workflow_name: str | None = None,
) -> dict:
    """Return a time-bucketed duration series for trend charting."""
    await asyncio.to_thread(workflow_stats.init_db)
    return await asyncio.to_thread(
        workflow_stats.get_timeseries,
        days,
        bucket_hours,
        repo,
        workflow_name,
    )


@router.post("/api/stats/workflows/collect")
async def collect_workflow_stats(
    principal: Principal = Depends(require_scope("workflows.control")),  # noqa: B008
) -> dict:
    """Trigger an on-demand poll of recent GitHub Actions runs for the org."""
    await asyncio.to_thread(workflow_stats.init_db)
    result = await workflow_stats.collect_once(ORG)
    log.info("workflow_stats.collect_once triggered by %s: %s", principal.id, result)
    return result
