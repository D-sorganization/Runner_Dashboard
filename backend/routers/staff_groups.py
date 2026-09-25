"""FastAPI router for Staff Groups and Board Deliberation (SC-B9, Issue #1339).

Routes:
  GET /groups                   List all available staff groups.
  GET /groups/{group_id}        Get details and seats for a specific group.
  GET /groups/{group_id}/cost-estimate
                                Estimate USD cost for a group turn before sending.
  POST /groups/{group_id}/threads
                                Convenience endpoint to create a group thread.
"""

# ruff: noqa: B008
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from identity import Principal, format_caller, require_scope
from staff.conversations import get_conversation_store
from staff.groups import (
    estimate_group_turn_cost,
    get_group,
    list_groups,
)

log = logging.getLogger("dashboard.staff.groups_router")

router = APIRouter(tags=["staff-groups"])


@router.get(
    "/groups",
    response_model_exclude_none=True,
    summary="List available staff groups",
)
async def get_groups(
    _caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """List all registered multi-seat staff groups (e.g. Board of Directors)."""
    groups = list_groups()
    return {"groups": [g.to_dict() for g in groups], "total": len(groups)}


@router.get(
    "/groups/{group_id}",
    response_model_exclude_none=True,
    summary="Get group details",
)
async def get_group_detail(
    group_id: str,
    _caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Get group specification, coordinator role, and seat definitions."""
    group = get_group(group_id)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Staff group '{group_id}' not found",
        )
    return group.to_dict()


@router.get(
    "/groups/{group_id}/cost-estimate",
    response_model_exclude_none=True,
    summary="Estimate group turn cost",
)
async def get_group_cost_estimate(
    group_id: str,
    prompt: str = Query(default="", description="Proposed question or prompt for the group"),
    _caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Estimate token spend and USD cost across all seats for a prompt."""
    group = get_group(group_id)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Staff group '{group_id}' not found",
        )
    estimate = estimate_group_turn_cost(group.id, prompt=prompt)
    return estimate.to_dict()


@router.post(
    "/groups/{group_id}/threads",
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_none=True,
    summary="Create a new group conversation thread",
)
async def create_group_thread_endpoint(
    group_id: str,
    title: str | None = None,
    caller: Principal = Depends(require_scope("staff.chat")),
) -> dict[str, Any]:
    """Convenience helper to create a group thread populated with coordinator and seats."""
    group = get_group(group_id)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Staff group '{group_id}' not found",
        )

    caller_id = format_caller(caller)
    participants = [group.coordinator] + [s.name for s in group.seats]
    if caller_id not in participants:
        participants.append(caller_id)

    thread_title = title or f"{group.name} Deliberation"
    store = get_conversation_store()
    rec = store.create_thread(
        title=thread_title,
        kind="group",
        participants=participants,
        created_by=caller_id,
        meta={
            "group": group.id,
            "coordinator": group.coordinator,
            "seats": [s.name for s in group.seats],
        },
    )
    return rec.to_dict()
