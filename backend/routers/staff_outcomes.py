"""Agent outcome scorecard API (WP-1.2, #1517), mounted under ``/api/v1/staff``.

  GET /outcomes?since=<ISO date>&group_by=role|provider|repo

Merge rate, verified rate, CI first-pass rate, fix-within-48h rate and cost per merged
PR for this node's staff runs (``staff.outcomes``). GitHub is read only for PRs the
runs recorded.
"""

# ruff: noqa: B008
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from identity import Principal, require_scope
from staff import outcomes
from staff.models import StaffOutcomesResponse
from staff.runner import get_runner

log = logging.getLogger("dashboard.staff.outcomes")
router = APIRouter(tags=["staff"])

RUN_LIMIT = 2000


def _checked_since(since: str | None) -> str:
    """The window start: ``since`` if it parses as an ISO date/time, else 422."""
    if not since:
        return outcomes.default_since()
    try:
        datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="since must be an ISO date or date-time") from exc
    return since


@router.get("/outcomes", response_model=StaffOutcomesResponse)
async def get_outcomes(
    since: str | None = Query(default=None, max_length=40),
    group_by: str = Query(default="role", max_length=10),
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Scorecard per ``group_by`` for runs created since ``since`` (default: 14 days)."""
    if group_by not in outcomes.GROUP_BY:
        raise HTTPException(status_code=422, detail=f"group_by must be one of {', '.join(outcomes.GROUP_BY)}")
    start = _checked_since(since)
    runner = get_runner()
    runs = runner.store.list_runs(limit=RUN_LIMIT, since=start)
    wanted, truncated = outcomes.prs_to_read(runs)
    facts = await outcomes.read_pr_facts(wanted)
    body = outcomes.aggregate(runs, facts, group_by=group_by)
    return {**body, "since": start, "machine": runner.machine, "prs_truncated": truncated}
