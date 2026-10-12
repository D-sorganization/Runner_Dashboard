"""Barb single-authority status (#1800, BR-06). Mounted under ``/api/v1/staff``."""

# ruff: noqa: B008
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from identity import Principal, require_scope
from staff.barb_runtime import barb_status

router = APIRouter(tags=["staff-barb"])


@router.get("/barb/status", summary="Barb authority role, epoch and outbox depth")
async def get_barb_status(_caller: Principal = Depends(require_scope("staff.read"))) -> dict[str, Any]:
    """Role (unset/authority/replica), epoch, outbox depth and whether delivery is degraded."""
    return barb_status()
