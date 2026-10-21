"""Email-derived work proposals at /api/v1/proposals."""

from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from identity import Principal, format_caller, principal_has_scope, require_principal
from intake_proposals.store import IntakeProposalStore, default_intake_proposal_store
from pydantic import BaseModel, Field
from staff.audit import record_audit
from staff.dispatch_service import DispatchCommand, dispatch_staff_run

log = logging.getLogger("dashboard.intake_proposals")

router = APIRouter(prefix="/api/v1/proposals", tags=["intake-proposals"])

SCOPE_CREATE = "intake.proposals.create"
SCOPE_DECIDE = "staff.approve"


class CreateIntakeProposalRequest(BaseModel):
    """Manual/testing create; production path is email-intake principal only."""

    subject: str = ""
    body_plain: str = Field(min_length=1)
    sender: str = ""
    message_id: str = ""
    dispatch: dict[str, Any] = Field(default_factory=dict)


class DecideIntakeProposalRequest(BaseModel):
    decision: str = Field(description="approved or rejected")
    reason: str = ""
    owner_approval_ref: str = Field(
        default="",
        description="Required when agent-grok approves; names Dieter's explicit approval.",
    )
    execute_dispatch: bool = Field(
        default=False,
        description="When approving, call staff dispatch (never automatic from intake).",
    )


def _store() -> IntakeProposalStore:
    return default_intake_proposal_store()


def _require_create(caller: Principal = Depends(require_principal)) -> Principal:  # noqa: B008
    if caller.id == "email-intake" and principal_has_scope(caller, SCOPE_CREATE):
        return caller
    if principal_has_scope(caller, "admin"):
        return caller
    raise HTTPException(status_code=403, detail="intake proposal create not permitted")


def _require_decide(caller: Principal = Depends(require_principal)) -> Principal:  # noqa: B008
    if principal_has_scope(caller, SCOPE_DECIDE) or principal_has_scope(caller, "admin"):
        return caller
    raise HTTPException(status_code=403, detail="proposal decision not permitted")


@router.get("")
def list_intake_proposals(
    state: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    store: IntakeProposalStore = Depends(_store),  # noqa: B008
    caller: Principal = Depends(require_principal),  # noqa: B008
) -> dict[str, Any]:
    if not (principal_has_scope(caller, SCOPE_DECIDE) or principal_has_scope(caller, "staff.read")):
        raise HTTPException(status_code=403, detail="forbidden")
    items = store.list(state=state, limit=limit)
    return {"proposals": [p.to_dict() for p in items]}


@router.get("/{proposal_id}")
def get_intake_proposal(
    proposal_id: str,
    store: IntakeProposalStore = Depends(_store),  # noqa: B008
    caller: Principal = Depends(require_principal),  # noqa: B008
) -> dict[str, Any]:
    if not (principal_has_scope(caller, SCOPE_DECIDE) or principal_has_scope(caller, "staff.read")):
        raise HTTPException(status_code=403, detail="forbidden")
    prop = store.get(proposal_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="not found")
    return prop.to_dict()


@router.post("")
def create_intake_proposal(
    body: CreateIntakeProposalRequest,
    store: IntakeProposalStore = Depends(_store),  # noqa: B008
    caller: Principal = Depends(_require_create),  # noqa: B008
) -> dict[str, Any]:
    prop = store.create_pending(
        subject=body.subject,
        body_plain=body.body_plain,
        sender=body.sender or caller.id,
        message_id=body.message_id or f"manual-{caller.id}",
        dispatch=body.dispatch,
    )
    record_audit(
        action="intake_proposal_create",
        target=prop.id,
        principal=caller.id,
        surface="api",
        outcome="success",
        detail={"manual": caller.id != "email-intake"},
    )
    return prop.to_dict()


@router.post("/{proposal_id}/decide")
async def decide_intake_proposal(
    proposal_id: str,
    body: DecideIntakeProposalRequest,
    store: IntakeProposalStore = Depends(_store),  # noqa: B008
    caller: Principal = Depends(_require_decide),  # noqa: B008
) -> dict[str, Any]:
    if body.decision not in {"approved", "rejected"}:
        raise HTTPException(status_code=422, detail="invalid decision")
    if body.decision == "approved" and caller.id == "agent-grok" and not body.owner_approval_ref.strip():
        raise HTTPException(status_code=422, detail="owner_approval_ref required for agent-grok approval")
    prop = store.decide(
        proposal_id,
        decision=body.decision,
        decided_by=format_caller(caller),
        reason=body.reason,
        owner_approval_ref=body.owner_approval_ref,
    )
    if prop is None:
        raise HTTPException(status_code=404, detail="not found or not pending")
    record_audit(
        action="intake_proposal_decide",
        target=proposal_id,
        principal=caller.id,
        surface="api",
        outcome=body.decision,
        detail={"owner_approval_ref": body.owner_approval_ref, "reason": body.reason},
    )
    if body.decision == "approved" and body.execute_dispatch:
        dispatch = prop.dispatch or {}
        role = str(dispatch.get("role") or "").strip()
        if not role:
            raise HTTPException(status_code=422, detail="dispatch.role required")
        cmd = DispatchCommand(
            role=role,
            requested_by=caller.id,
            repo=str(dispatch.get("repo") or ""),
            issue=dispatch.get("issue"),
            prompt=str(dispatch.get("prompt") or prop.body_plain),
            surface="api",
        )
        try:
            result = await dispatch_staff_run(cmd, caller)
        except HTTPException:
            raise
        return {"proposal": prop.to_dict(), "dispatch": result}
    return prop.to_dict()
