"""Staff Inbox and Barb Briefing API endpoints (SC-C5, Issue #1328).

Provides:
- GET /api/v1/staff/inbox: Aggregated 'Waiting on you' items with fault isolation.
- POST /api/v1/staff/briefing: Generate and post morning/evening/on-demand briefing.
"""

# ruff: noqa: B008
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from identity import Principal, format_caller, require_scope
from pydantic import BaseModel, Field
from staff.inbox import collect_inbox, post_briefing_to_barb

log = logging.getLogger("dashboard.staff.inbox_router")

router = APIRouter(prefix="/api/staff", tags=["staff-inbox"])
v1_router = APIRouter(prefix="/api/v1/staff", tags=["staff-inbox-v1"])


class PostBriefingBody(BaseModel):
    """Request payload for triggering a briefing."""

    kind: Literal["morning", "evening", "on_demand"] = Field(
        default="on_demand", description="Briefing kind (morning, evening, on_demand)"
    )


@router.get("/inbox", response_model_exclude_none=True)
@v1_router.get("/inbox", response_model_exclude_none=True)
async def get_staff_inbox(
    caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Retrieve aggregated 'Waiting on you' inbox items requiring operator attention."""
    try:
        caller_id = format_caller(caller)
        inbox = await collect_inbox(caller_id=caller_id)
        return inbox.to_dict()
    except Exception as exc:
        log.exception("Unexpected error collecting staff inbox: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed collecting staff inbox: {exc}") from exc


@router.post("/briefing")
@v1_router.post("/briefing")
async def trigger_briefing(
    body: PostBriefingBody,
    caller: Principal = Depends(require_scope("staff.chat")),
) -> dict[str, Any]:
    """Generate and post an on-schedule or on-demand briefing to Barb's thread."""
    try:
        res = await post_briefing_to_barb(kind=body.kind)
        return res
    except Exception as exc:
        log.exception("Failed posting staff briefing: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed posting staff briefing: {exc}") from exc
