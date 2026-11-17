"""Read-only status of the review-arm service (Refs #4249)."""

# ruff: noqa: B008

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from identity import Principal, require_scope
from staff.review_arm import runtime

router = APIRouter(prefix="/api/staff", tags=["staff"])


@router.get("/review-arm/status")
async def review_arm_status(_peer: Principal = Depends(require_scope("staff.read"))) -> dict[str, Any]:
    """enabled, dry_run, host, queue depth, last intake/dispatch/verdict/arm times, recent decisions."""
    return runtime.status()
