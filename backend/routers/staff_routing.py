"""FastAPI router for Staff Routing, Handoffs, and Overrides (SC-C2 #1315)."""

# ruff: noqa: B008
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from identity import Principal, format_caller, require_scope
from pydantic import BaseModel, Field
from routers.staff_threads import _get_store_or_503
from staff.conversations import (
    ConversationsUnavailableError,
)
from staff.router import (
    BarbRouter,
    RoutingDecision,
)

log = logging.getLogger("dashboard.staff.routing")

router = APIRouter(tags=["staff-routing"])


# ── REQUEST MODELS ───────────────────────────────────────────────────────────


class RoutingDecideRequest(BaseModel):
    """Request payload to evaluate routing for a prompt."""

    text: str = Field(..., min_length=1, description="Prompt or message content to route")
    thread_id: str | None = Field(default=None, description="Optional thread context ID")


class RoutingHandoffRequest(BaseModel):
    """Request payload to execute a handoff to a destination role."""

    text: str = Field(..., min_length=1, description="Original request content")
    target_role: str = Field(..., min_length=1, description="Destination role name")
    source_thread_id: str | None = Field(default=None, description="Originating thread ID")
    reason: str | None = Field(default=None, description="Handoff rationale")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    mode: str | None = Field(default=None)


class RoutingOverrideRequest(BaseModel):
    """Request payload to override an existing handoff decision."""

    handoff_message_id: str = Field(..., min_length=1, description="ID of the handoff message")
    target_role: str = Field(..., min_length=1, description="New target role name")
    reason: str = Field(..., min_length=1, description="Reason for the routing override")


# ── ENDPOINTS ────────────────────────────────────────────────────────────────


@router.post(
    "/routing/decide",
    response_model_exclude_none=True,
)
async def decide_routing(
    body: RoutingDecideRequest,
    caller: Principal = Depends(require_scope("staff.chat")),
) -> dict[str, Any]:
    """Evaluate two-stage routing decision for a prompt without executing handoff."""
    _get_store_or_503()
    caller_id = format_caller(caller)
    barb_router = BarbRouter()
    decision = barb_router.route(body.text, user_id=caller_id)
    return decision.to_dict()


@router.post(
    "/routing/handoff",
    response_model_exclude_none=True,
)
async def execute_handoff(
    body: RoutingHandoffRequest,
    caller: Principal = Depends(require_scope("staff.chat")),
) -> dict[str, Any]:
    """Execute a handoff to a destination role, creating target thread and work item."""
    store = _get_store_or_503()
    caller_id = format_caller(caller)
    barb_router = BarbRouter()

    decision = RoutingDecision(
        chosen_role=body.target_role,
        confidence=body.confidence if body.confidence is not None else 1.0,
        reason=body.reason or f"Routed to {body.target_role}",
        mode=body.mode or "manual",
    )

    try:
        result = barb_router.execute_handoff(
            decision=decision,
            original_message=body.text,
            caller_id=caller_id,
            source_thread_id=body.source_thread_id,
            store=store,
        )
        return result.to_dict()
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("Error executing routing handoff: %s", exc)
        raise HTTPException(status_code=500, detail=f"Handoff failed: {exc}") from exc


@router.post(
    "/routing/override",
    response_model_exclude_none=True,
)
async def override_routing(
    body: RoutingOverrideRequest,
    caller: Principal = Depends(require_scope("staff.chat")),
) -> dict[str, Any]:
    """Apply an owner override to a handoff message and log routing feedback."""
    store = _get_store_or_503()
    caller_id = format_caller(caller)
    barb_router = BarbRouter()

    try:
        record = barb_router.override_routing(
            handoff_message_id=body.handoff_message_id,
            new_target_role=body.target_role,
            reason=body.reason,
            overridden_by=caller_id,
            store=store,
        )
        return record.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("Error overriding routing: %s", exc)
        raise HTTPException(status_code=500, detail=f"Override failed: {exc}") from exc


@router.get(
    "/routing/feedback",
    response_model_exclude_none=True,
)
async def list_routing_feedback(
    limit: int = Query(default=50, ge=1, le=200),
    caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """List recent routing feedback records captured from overrides (SC-C7)."""
    store = _get_store_or_503()
    barb_router = BarbRouter()
    items = barb_router.list_routing_feedback(store=store, limit=limit)
    return {"items": [fb.to_dict() for fb in items], "count": len(items)}


@router.get(
    "/routing/eval",
    response_model_exclude_none=True,
)
async def get_routing_evaluation(
    caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Retrieve the latest Barb routing evaluation result (SC-C7)."""
    from staff.routing_eval import get_latest_routing_eval  # noqa: PLC0415

    eval_data = get_latest_routing_eval()
    if eval_data is None:
        return {"status": "none", "message": "No evaluation has been recorded yet", "result": None}
    return {"status": "ok", "result": eval_data}
