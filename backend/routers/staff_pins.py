# ruff: noqa: B008
"""Router for staff role pins persistence (/api/v1/staff/pins) (SC-D3, Issue #1317).

Endpoints:
- GET /api/v1/staff/pins: returns current user's pinned roles
- POST /api/v1/staff/pins/{role}: pins a role, returns updated pins list
- DELETE /api/v1/staff/pins/{role}: unpins a role, returns updated pins list
- PUT /api/v1/staff/pins: replaces all pins for current user
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from identity import Principal, format_caller, require_scope
from pydantic import BaseModel, Field
from staff.pins import get_pins_store

log = logging.getLogger("dashboard.staff.pins.router")

router = APIRouter(prefix="/pins", tags=["staff-pins"])


class SetPinsRequest(BaseModel):
    pins: list[str] = Field(default_factory=list, description="List of role names to pin.")


class PinsResponse(BaseModel):
    pins: list[str] = Field(default_factory=list, description="List of pinned role names.")


def _get_user_id(caller: Principal | None) -> str:
    if caller is not None:
        user_id = format_caller(caller)
        if user_id:
            return user_id
    return "default"


@router.get("", response_model=PinsResponse)
async def get_pins(
    caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Retrieve pinned roles for the calling user."""
    store = get_pins_store()
    user_id = _get_user_id(caller)
    return {"pins": store.get_pins(user_id)}


@router.post("/{role}", response_model=PinsResponse)
async def pin_role(
    role: str,
    caller: Principal = Depends(require_scope("staff.chat")),
) -> dict[str, Any]:
    """Pin a role for the calling user."""
    store = get_pins_store()
    user_id = _get_user_id(caller)
    return {"pins": store.pin_role(user_id, role)}


@router.delete("/{role}", response_model=PinsResponse)
async def unpin_role(
    role: str,
    caller: Principal = Depends(require_scope("staff.chat")),
) -> dict[str, Any]:
    """Unpin a role for the calling user."""
    store = get_pins_store()
    user_id = _get_user_id(caller)
    return {"pins": store.unpin_role(user_id, role)}


@router.put("", response_model=PinsResponse)
async def set_pins(
    body: SetPinsRequest,
    caller: Principal = Depends(require_scope("staff.chat")),
) -> dict[str, Any]:
    """Replace all pinned roles for the calling user."""
    store = get_pins_store()
    user_id = _get_user_id(caller)
    return {"pins": store.set_pins(user_id, body.pins)}
