"""Staff Proposals and Actions API endpoints (SC-B6 #1313, SC-F4 #1323)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from identity import Principal, format_caller, require_scope
from pydantic import BaseModel, Field
from routers.staff_threads import _get_store_or_503
from staff.actions import (
    ACTION_REGISTRY,
    ProposalExpiredError,
    ProposalReplayError,
    RolePermissionDeniedError,
    execute_proposal,
)
from staff.conversations import ConversationsUnavailableError
from staff.pagination import paginate_items

log = logging.getLogger("dashboard.staff.proposals")

router = APIRouter(tags=["staff-proposals"])


class CreateProposalRequest(BaseModel):
    message_id: str = Field(description="Originating message ID")
    thread_id: str = Field(description="Parent thread ID")
    action: str = Field(description="Name of allowlisted action")
    params: dict[str, Any] = Field(default_factory=dict, description="Action parameters")
    risk: str = Field(default="low", description="Risk class (read, low, medium, high, owner-only)")


class DecideProposalRequest(BaseModel):
    decision: str = Field(description="Must be 'approved' or 'denied'")
    reason: str = Field(default="", description="Reason for the decision")
    execute: bool = Field(
        default=False,
        description="Whether to execute the action immediately upon approval",
    )


# ─── Actions Catalogue Endpoints ─────────────────────────────────────────────


@router.get("/actions", response_model_exclude_none=True)
async def list_actions(
    _caller: Principal = Depends(require_scope("staff.read")),  # noqa: B008
) -> dict[str, Any]:
    """List all allowlisted staff actions, schemas, risk classes, and required scopes."""
    actions = ACTION_REGISTRY.list_actions()
    items = [a.to_dict() for a in actions]
    return {"items": items, "actions": items, "total": len(items)}


@router.get("/actions/{action_name:path}", response_model_exclude_none=True)
async def get_action(
    action_name: str,
    _caller: Principal = Depends(require_scope("staff.read")),  # noqa: B008
) -> dict[str, Any]:
    """Get specification for a single allowlisted staff action."""
    act = ACTION_REGISTRY.get(action_name)
    if not act:
        raise HTTPException(status_code=404, detail=f"Action '{action_name}' not found")
    return act.to_dict()


# ─── Proposals Endpoints ─────────────────────────────────────────────────────


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


@router.post(
    "/proposals",
    response_model_exclude_none=True,
)
async def create_proposal(
    body: CreateProposalRequest,
    caller: Principal = Depends(require_scope("staff.read")),  # noqa: B008
) -> dict[str, Any]:
    """Create a new action proposal within a conversation thread."""
    store = _get_store_or_503()
    try:
        prop = store.create_proposal(
            message_id=body.message_id,
            thread_id=body.thread_id,
            action=body.action,
            params=body.params,
            risk=body.risk,
            principal=format_caller(caller),
        )
        return prop.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
    """Decide (approve or deny) an action proposal, optionally executing immediately."""
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

        if body.decision == "approved" and body.execute:
            res = execute_proposal(proposal_id=proposal_id, approver=caller, store=store)
            refreshed = store.get_proposal(proposal_id)
            d = refreshed.to_dict() if refreshed else updated.to_dict()
            d["execution_result"] = res.to_dict()
            return d

        return updated.to_dict()
    except ProposalExpiredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RolePermissionDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/proposals/{proposal_id}/execute",
    response_model_exclude_none=True,
)
async def execute_approved_proposal(
    proposal_id: str,
    caller: Principal = Depends(require_scope("staff.approve")),  # noqa: B008
) -> dict[str, Any]:
    """Execute an approved proposal through the action registry."""
    store = _get_store_or_503()
    prop = store.get_proposal(proposal_id)
    if not prop:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")

    if prop.state in ("denied", "expired", "done"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Proposal {proposal_id} in state '{prop.state}' cannot be executed",
        )

    try:
        res = execute_proposal(proposal_id=proposal_id, approver=caller, store=store)
        refreshed = store.get_proposal(proposal_id)
        d = refreshed.to_dict() if refreshed else prop.to_dict()
        d["execution_result"] = res.to_dict()
        return d
    except ProposalExpiredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RolePermissionDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ProposalReplayError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("Error executing proposal %s: %s", proposal_id, exc)
        raise HTTPException(status_code=500, detail=f"Execution error: {exc}") from exc


class DetectStalledRequest(BaseModel):
    queued_runs: list[dict[str, Any]] | None = None
    in_progress_runs: list[dict[str, Any]] | None = None
    runners: list[dict[str, Any]] | None = None
    known_hosts: list[str] | None = None
    auto_remediate: bool = True


@router.post("/maintenance/detect-stalled")
async def detect_stalled_jobs(
    body: DetectStalledRequest | None = None,
    caller: Principal = Depends(require_scope("staff.read")),  # noqa: B008
) -> dict[str, Any]:
    """Execute stalled-job detection scan with optional auto-remediation (SC-E5)."""
    from staff.maintenance_detect import StalledJobDetector

    req = body or DetectStalledRequest()
    detector = StalledJobDetector()
    report = detector.run_scan(
        queued_runs=req.queued_runs,
        in_progress_runs=req.in_progress_runs,
        runners=req.runners,
        known_hosts=set(req.known_hosts) if req.known_hosts else None,
        auto_remediate=req.auto_remediate,
        caller=caller,
    )
    return report.to_dict()
