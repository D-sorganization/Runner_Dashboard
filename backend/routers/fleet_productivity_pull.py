"""GET /api/v1/fleet/productivity-pull — 5 PM fleet productivity JSON snapshot
(issue #3641, epic #3295 Grok Bot offload)."""

# ruff: noqa: B008
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from identity import require_fleet_peer
from staff.fleet_productivity_pull import pull_snapshot

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet-productivity"])


@router.get("/productivity-pull")
async def get_productivity_pull(_peer: str = Depends(require_fleet_peer)) -> Any:
    return await asyncio.to_thread(pull_snapshot)
