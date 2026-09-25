"""Board routing gate router for Code Requests (CR-6, issue #1286).

Endpoints:
- POST /api/code-requests/{id}/evaluate-board: evaluate whether a Code Request routes to the Board
- POST /api/code-requests/{id}/route-to-board: route Code Request to Board (creating proposal) or planning
- POST /api/code-requests/{id}/sync-board-decision: sync Board proposal decision to Code Request
- GET /api/code-requests/{id}/board-escalation: check escalation deadline for unreviewed proposals
"""

from __future__ import annotations

import logging
from typing import Any

from code_requests.board_gate import (
    BoardRoutingCriteria,
    check_board_escalation,
    evaluate_board_routing,
    route_code_request_to_board,
    sync_board_proposal_decision,
)
from code_requests.model import BoardRoute, CodeRequestState
from fastapi import APIRouter, Depends, HTTPException
from identity import Principal, principal_has_scope, require_scope
from pydantic import BaseModel, Field
from routers.code_requests import _get_store

log = logging.getLogger("dashboard.code_requests.board")

router = APIRouter(tags=["code-requests-board"])


class EvaluateBoardPayload(BaseModel):
    criteria: BoardRoutingCriteria = Field(default_factory=BoardRoutingCriteria)
    board_route: BoardRoute | None = None


class RouteToBoardPayload(BaseModel):
    criteria: BoardRoutingCriteria = Field(default_factory=BoardRoutingCriteria)
    board_route: BoardRoute | None = None
    is_operator_override: bool = False
    override_reason: str = ""


class SyncBoardPayload(BaseModel):
    pass


@router.post("/api/code-requests/{id}/evaluate-board")
async def evaluate_board(
    id: str,
    payload: EvaluateBoardPayload,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict[str, Any]:
    """Evaluate whether a Code Request must go through Architecture Board review."""
    store = _get_store()
    request = await store.get(id)
    if request is None:
        raise HTTPException(status_code=404, detail=f"Code Request {id!r} not found")

    eval_request = request
    if payload.board_route is not None:
        eval_request = request.model_copy(update={"board_route": payload.board_route})

    decision = evaluate_board_routing(eval_request, criteria=payload.criteria)
    return {"decision": decision.model_dump(mode="json")}


@router.post("/api/code-requests/{id}/route-to-board")
async def route_to_board(
    id: str,
    payload: RouteToBoardPayload,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict[str, Any]:
    """Execute the Board routing gate for a Code Request at triage."""
    store = _get_store()
    request = await store.get(id)
    if request is None:
        raise HTTPException(status_code=404, detail=f"Code Request {id!r} not found")

    if payload.is_operator_override:
        if not principal_has_scope(principal, "operator"):
            raise HTTPException(
                status_code=403,
                detail="Operator scope required for force_board or skip_board overrides",
            )
        if not payload.override_reason:
            raise HTTPException(
                status_code=422,
                detail="override_reason required for operator overrides",
            )

    eval_request = request
    if payload.board_route is not None:
        eval_request = request.model_copy(update={"board_route": payload.board_route})

    decision = evaluate_board_routing(eval_request, criteria=payload.criteria)
    actor = principal.id or principal.name or "operator"

    if decision.routes_to_board:
        from proposals.models import CreateProposalRequest  # noqa: PLC0415
        from proposals.service import create_proposal  # noqa: PLC0415

        req_model = CreateProposalRequest(
            title=eval_request.title,
            target_repos=[eval_request.repository],
            problem=eval_request.prompt,
            evidence=f"Code Request triggers Board review: {'; '.join(decision.reasons) or 'Architecture trigger'}",
            options_considered=(
                f"1. Implement as requested: {eval_request.title}\n2. Reject or defer based on Board review."
            ),
            lean=f"Routed automatically to Board: {'; '.join(decision.reasons)}",
            estimated_cost="Medium",
            urgency="Routine",
            code_request_url=f"https://github.com/D-sorganization/Runner_Dashboard/issues/{eval_request.issue_number}",
            source=eval_request.requester.id,
            confirm_not_duplicate=True,
        )
        proposal_item = await create_proposal(req=req_model, caller=principal)

        updated = route_code_request_to_board(
            eval_request,
            decision=decision,
            create_proposal_fn=lambda **kwargs: proposal_item,
            actor=actor,
        )
        saved = await store.save(updated)
        return {
            "routed": True,
            "decision": decision.model_dump(mode="json"),
            "request": saved.model_dump(mode="json"),
        }

    # Routine work skips board -> transition directly to planning
    updated = await store.transition(
        id,
        CodeRequestState.PLANNING,
        actor=actor,
        reason=f"Board gate bypassed: routine work ({'; '.join(decision.reasons) or 'no triggers'})",
        is_operator_override=payload.is_operator_override,
    )
    return {
        "routed": False,
        "decision": decision.model_dump(mode="json"),
        "request": updated.model_dump(mode="json"),
    }


@router.post("/api/code-requests/{id}/sync-board-decision")
async def sync_board_decision(
    id: str,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict[str, Any]:
    """Check decision on linked Board proposal and transition Code Request accordingly."""
    store = _get_store()
    request = await store.get(id)
    if request is None:
        raise HTTPException(status_code=404, detail=f"Code Request {id!r} not found")

    if not request.board_proposal:
        return {
            "synced": False,
            "detail": "Code Request does not have a linked Board proposal",
            "request": request.model_dump(mode="json"),
        }

    try:
        from proposals.service import get_proposal  # noqa: PLC0415

        prop_num = int(request.board_proposal)
        prop_detail = await get_proposal(prop_num)
        prop_dict = prop_detail.model_dump(mode="json")
    except Exception as exc:
        log.warning("Could not fetch proposal %s: %s", request.board_proposal, exc)
        return {
            "synced": False,
            "detail": f"Failed to fetch proposal {request.board_proposal}: {exc}",
            "request": request.model_dump(mode="json"),
        }

    actor = principal.id or principal.name or "board-sync"
    updated = sync_board_proposal_decision(
        request,
        get_proposal_fn=lambda _num: prop_dict,
        actor=actor,
    )

    if updated.state != request.state:
        saved = await store.save(updated)
        return {
            "synced": True,
            "decision": str(saved.state.value),
            "request": saved.model_dump(mode="json"),
        }

    return {
        "synced": False,
        "decision": None,
        "request": request.model_dump(mode="json"),
    }


@router.get("/api/code-requests/{id}/board-escalation")
async def check_escalation(
    id: str,
    meetings_elapsed: int = 0,
    max_meetings: int = 2,
    *,
    principal: Principal = Depends(require_scope("code-requests.manage")),  # noqa: B008
) -> dict[str, Any]:
    """Check if the Code Request proposal has passed its review escalation deadline."""
    store = _get_store()
    request = await store.get(id)
    if request is None:
        raise HTTPException(status_code=404, detail=f"Code Request {id!r} not found")

    escalation = check_board_escalation(request, meetings_elapsed=meetings_elapsed, max_meetings=max_meetings)
    return {
        "id": request.id,
        "state": request.state.value,
        "meetings_elapsed": meetings_elapsed,
        "max_meetings": max_meetings,
        "escalation_needed": escalation,
    }
