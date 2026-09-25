"""FastAPI router for Staff Work Items API (SC-C3, Issue #1316).

Endpoints:
- POST /api/v1/staff/work-items: Create a tracked work item
- GET /api/v1/staff/work-items: Query work items with filtering (mine, overdue, state, thread_id)
- GET /api/v1/staff/work-items/{id}: Retrieve work item details with links and progress timestamps
- PATCH /api/v1/staff/work-items/{id}: Mutate work item fields or advance state machine
"""

# ruff: noqa: B008
from __future__ import annotations

import logging
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from identity import Principal, format_caller, require_scope
from pydantic import BaseModel, Field
from staff.pagination import paginate_items
from staff.work_items import (
    InvalidStateTransitionError,
    get_work_item_store,
)

log = logging.getLogger("dashboard.staff.work_items_router")

router = APIRouter(tags=["staff-work-items"])


class CreateWorkItemRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    thread_id: str | None = None
    owner_role: str | None = None
    expected_by: str | None = None
    links: dict[str, list[str]] = Field(default_factory=dict)
    next_check_at: str | None = None


class PatchWorkItemRequest(BaseModel):
    title: str | None = None
    owner_role: str | None = None
    expected_by: str | None = None
    next_check_at: str | None = None
    state: str | None = None
    reason: str | None = None


@router.post(
    "/work-items",
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_none=True,
)
async def create_work_item(
    body: CreateWorkItemRequest,
    caller: Principal = Depends(require_scope("staff.chat")),
) -> dict[str, Any]:
    """Create and persist a new tracked work item."""
    store = get_work_item_store()
    caller_id = format_caller(caller)

    item = store.create_work_item(
        title=body.title,
        requested_by=caller_id,
        thread_id=body.thread_id or "",
        owner_role=body.owner_role or "",
        expected_by=body.expected_by,
        links=body.links or {},
        next_check_at=body.next_check_at,
    )
    return item.to_dict()


@router.get(
    "/work-items",
    response_model_exclude_none=True,
)
async def list_work_items(
    thread_id: str | None = Query(default=None),
    state: str | None = Query(default=None),
    owner_role: str | None = Query(default=None),
    requested_by: str | None = Query(default=None),
    mine: bool | None = Query(default=None),
    waiting_on_me: bool | None = Query(default=None),
    overdue: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """List work items matching optional filters and cursor pagination."""
    store = get_work_item_store()
    caller_id = format_caller(caller)

    req_by = caller_id if mine else requested_by

    items = store.list_work_items(
        thread_id=thread_id,
        owner_role=owner_role,
        requested_by=req_by,
        state=state,
        overdue=overdue,
        limit=200,
    )

    if waiting_on_me:
        items = [
            it
            for it in items
            if it.state == "waiting_on_user" and (it.requested_by == caller_id or caller_id in it.requested_by)
        ]

    dicts = [item.to_dict() for item in items]
    page = paginate_items(
        dicts,
        cursor=cursor,
        limit=limit,
        key_fn=lambda d: (
            str(d.get("created_at", "")),
            str(d.get("id", "")),
        ),
    )
    return {
        "items": page.items,
        "work_items": page.items,
        "next_cursor": page.next_cursor,
        "prev_cursor": page.prev_cursor,
        "has_more": page.has_more,
    }


@router.get(
    "/work-items/{work_item_id}",
    response_model_exclude_none=True,
)
async def get_work_item(
    work_item_id: str,
    _caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Retrieve details for a single work item."""
    store = get_work_item_store()
    item = store.get_work_item(work_item_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "work_item_not_found", "message": f"Work item '{work_item_id}' not found"},
        )
    return item.to_dict()


@router.patch(
    "/work-items/{work_item_id}",
    response_model_exclude_none=True,
)
async def patch_work_item(
    work_item_id: str,
    body: PatchWorkItemRequest,
    caller: Principal = Depends(require_scope("staff.chat")),
) -> dict[str, Any]:
    """Update work item metadata or advance its lifecycle state."""
    store = get_work_item_store()
    caller_id = format_caller(caller)

    current = store.get_work_item(work_item_id)
    if not current:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "work_item_not_found", "message": f"Work item '{work_item_id}' not found"},
        )

    # 1. Handle state transition if requested
    if body.state and body.state != current.state:
        try:
            store.transition_state(
                work_item_id,
                body.state,
                actor=caller_id,
                reason=body.reason or "",
            )
        except InvalidStateTransitionError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "invalid_transition", "message": str(exc)},
            ) from exc

    # 2. Handle mutable metadata updates
    if any(v is not None for v in (body.title, body.owner_role, body.expected_by, body.next_check_at)):
        store.update_work_item(
            work_item_id,
            title=body.title,
            owner_role=body.owner_role,
            expected_by=body.expected_by,
            next_check_at=body.next_check_at,
        )

    updated = store.get_work_item(work_item_id)
    assert updated is not None  # noqa: S101
    return updated.to_dict()
