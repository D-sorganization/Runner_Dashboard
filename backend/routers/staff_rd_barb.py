"""rd-barb and persona role health read/heartbeat API for Grok Barb (RD #3065, #3075, #3295 S3, #3296).

Agent-facing, so not in the generated client (include_in_schema=False).
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from identity import Principal, require_scope
from pydantic import BaseModel, Field
from staff.rd_barb_inbox import DashboardInbox, default_inbox_path
from staff.rd_barb_memory import GuidelinesDoc, default_guidelines_path
from staff.rd_barb_notify_health import NotifyHealth, default_health_path
from staff.rd_barb_policy import LockedPolicyError, load_locked_policy
from staff.role_health import RoleHealthStore, default_role_health_path

router = APIRouter(prefix="/api/v1/staff", tags=["staff", "rd-barb"], include_in_schema=False)


class RoleHeartbeatRequest(BaseModel):
    epoch: int = Field(default=1, ge=1)
    slot_started_at: float | None = None
    expected_slot_at: float | None = None
    chain_exhausted: bool | None = None
    now: float | None = None


def guidelines_doc() -> GuidelinesDoc:
    return GuidelinesDoc(default_guidelines_path(), load_locked_policy())


def role_health_store() -> RoleHealthStore:
    return RoleHealthStore(default_role_health_path())


@router.get("/rd-barb/guidelines")
def get_guidelines(_principal: Principal = Depends(require_scope("fleet.read"))) -> dict[str, Any]:  # noqa: B008
    """Rendered guidelines doc plus ``size_bytes`` and ``compacted_at``; entries are secret-linted on write."""
    try:
        doc = guidelines_doc()
    except LockedPolicyError as exc:  # fail closed: no policy, no doc
        raise HTTPException(status_code=503, detail="rd-barb locked policy unavailable") from exc
    text = doc.render()
    return {"doc": text, "size_bytes": len(text.encode("utf-8")), "compacted_at": doc.compacted_at}


@router.get("/rd-barb/notify-status")
def get_notify_status(_principal: Principal = Depends(require_scope("fleet.read"))) -> dict[str, Any]:  # noqa: B008
    """Direct notify delivery health: ``failing`` from the first failure, ``degraded`` after 5 minutes (#3075)."""
    return NotifyHealth(default_health_path()).status(now=time.time())


@router.get("/rd-barb/inbox")
def get_inbox(_principal: Principal = Depends(require_scope("fleet.read"))) -> dict[str, Any]:  # noqa: B008
    """Unacknowledged questions and alerts delivered to the dashboard channel, newest first (#3075)."""
    items = DashboardInbox(default_inbox_path()).unacknowledged()
    return {"unacknowledged": len(items), "items": items}


@router.get("/roles/health")
def get_roles_health(
    roles: str | None = Query(default=None, max_length=256),
    now: float | None = Query(default=None),
    _principal: Principal = Depends(require_scope("fleet.read")),  # noqa: B008
) -> dict[str, Any]:
    """Aggregate persona role health with 2-reading hysteresis; unknown roles fail closed to ``degraded`` (#3295 S3)."""
    role_list = [r.strip() for r in roles.split(",") if r.strip()] if roles else None
    try:
        return role_health_store().aggregate_status(roles=role_list, now=now)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/roles/{role}/heartbeat")
def post_role_heartbeat(
    role: str,
    body: RoleHeartbeatRequest,
    _principal: Principal = Depends(require_scope("staff.dispatch")),  # noqa: B008
) -> dict[str, Any]:
    """Record a persona role heartbeat and return its updated health snapshot (#3295 S3)."""
    try:
        return role_health_store().record_heartbeat(
            role,
            epoch=body.epoch,
            slot_started_at=body.slot_started_at,
            expected_slot_at=body.expected_slot_at,
            chain_exhausted=body.chain_exhausted,
            now=body.now,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
