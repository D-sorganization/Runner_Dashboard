"""Staff Proposals API endpoints (SC-F4, Issue #1323)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from identity import Principal, format_caller, require_scope
from pydantic import BaseModel, Field
from routers.staff_threads import _get_store_or_503
from staff.conversations import ConversationsUnavailableError
from staff.pagination import paginate_items

log = logging.getLogger("dashboard.staff.proposals")

router = APIRouter(tags=["staff-proposals"])


class DecideProposalRequest(BaseModel):
    decision: str = Field(description="Must be 'approved' or 'denied'")
    reason: str = Field(default="", description="Reason for the decision")


@router.get(
    "/proposals",
    response_model_exclude_none=True,
)
async def list_proposals(
    thread_id: str | None = Query(default=None),
    message_id: str | None = Query(default=None),
    state: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    _caller: Principal = Depends(require_scope("staff.read")),  # noqa: B008
) -> dict[str, Any]:
    """List action proposals with optional filtering by thread, message, state."""
    store = _get_store_or_503()
    try:
        proposals = store.list_proposals(
            thread_id=thread_id,
            message_id=message_id,
            state=state,
            limit=limit * 2,
        )
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    dicts = [p.to_dict() for p in proposals]
    page = paginate_items(
        dicts,
        cursor=cursor,
        limit=limit,
        key_fn=lambda d: (str(d.get("created_at") or ""), str(d.get("id", ""))),
    )
    return {
        "items": page.items,
        "proposals": page.items,
        "next_cursor": page.next_cursor,
        "prev_cursor": page.prev_cursor,
        "has_more": page.has_more,
    }


@router.get(
    "/proposals/{proposal_id}",
    response_model_exclude_none=True,
)
async def get_proposal(
    proposal_id: str,
    _caller: Principal = Depends(require_scope("staff.read")),  # noqa: B008
) -> dict[str, Any]:
    """Get single action proposal by ID."""
    store = _get_store_or_503()
    try:
        prop = store.get_proposal(proposal_id)
        if not prop:
            raise HTTPException(status_code=404, detail="proposal not found")
        return prop.to_dict()
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/proposals/{proposal_id}/decide",
    response_model_exclude_none=True,
)
async def decide_proposal(
    proposal_id: str,
    body: DecideProposalRequest,
    caller: Principal = Depends(require_scope("staff.approve")),  # noqa: B008
) -> dict[str, Any]:
    """Decide (approve or deny) an action proposal."""
    if body.decision not in ("approved", "denied"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Decision must be 'approved' or 'denied', got '{body.decision}'",
        )

    store = _get_store_or_503()
    caller_id = format_caller(caller)
    try:
        prop = store.get_proposal(proposal_id)
        if not prop:
            raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")
        if prop.state != "proposed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot decide proposal in state '{prop.state}' (must be 'proposed')",
            )
        updated = store.decide_proposal(
            proposal_id=proposal_id,
            state=body.decision,
            decided_by=caller_id,
            reason=body.reason,
        )
        return updated.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
