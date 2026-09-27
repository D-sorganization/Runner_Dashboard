"""FastAPI router for expert panels (#1634, epic #1633). Mounted under ``/api/v1/staff``.

Routes:
  POST /panels                 Start a panel (3-4 experts, N rounds, debate or brainstorm).
  GET  /panels/presets         Ready-made expert line-ups and the providers a seat may use.
  GET  /panels/{thread_id}     Turns, stances, consensus and the moderator's synthesis.
"""

# ruff: noqa: B008
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from identity import Principal, format_caller, require_scope
from staff.audit import record_audit
from staff.conversations import ConversationsUnavailableError, get_conversation_store
from staff.panel import (
    TurnRunner,
    active_panel_count,
    default_turn_runner,
    estimate_panel_cost,
    panel_result,
    run_panel,
    start_panel_thread,
)
from staff.panel_models import MAX_ACTIVE_PANELS, PANEL_PRESETS, PanelCreateRequest, enabled_panel_providers
from staff.rate_limit import check_rate_limit

log = logging.getLogger("dashboard.staff.panels_router")

router = APIRouter(tags=["staff-panels"])
_BACKGROUND: set[asyncio.Task[None]] = set()  # strong references so running panels are not collected


def _turn_runner() -> TurnRunner:
    """The runner every panel turn uses; tests replace this seam."""
    return default_turn_runner


@router.post("/panels", status_code=status.HTTP_202_ACCEPTED, summary="Start an expert panel")
async def create_panel(
    body: PanelCreateRequest,
    caller: Principal = Depends(require_scope("staff.chat")),
) -> dict[str, Any]:
    """Create a panel thread and run it in the background.

    Pre: ``body`` passed :class:`PanelCreateRequest`; fewer than ``MAX_ACTIVE_PANELS`` are running;
    the estimate is under the group cost threshold or ``confirm_cost`` is true.
    Post: 202 with the thread and the estimate; the turns arrive on the thread's stream.
    """
    check_rate_limit("dispatches", caller)
    if active_panel_count() >= MAX_ACTIVE_PANELS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "panel_limit", "message": f"{MAX_ACTIVE_PANELS} panels are already running"},
        )
    estimate = estimate_panel_cost(body)
    if estimate.exceeds_threshold and not body.confirm_cost:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "group_cost_guard_threshold_exceeded",
                "message": estimate.warning,
                "estimate": estimate.to_dict(),
                "retryable": True,
            },
        )
    caller_id = format_caller(caller)
    try:
        thread = start_panel_thread(body, created_by=caller_id, store=get_conversation_store())
    except ConversationsUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    task = asyncio.create_task(run_panel(thread.id, body, runner=_turn_runner()))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    record_audit(
        action="panel_started",
        target=f"panel:{thread.id}",
        principal=caller_id,
        surface="thread",
        thread_id=thread.id,
        detail={"experts": [e.name for e in body.experts], "rounds": body.rounds, "mode": body.mode},
        fail_closed=False,
    )
    return {"thread": thread.to_dict(), "estimate": estimate.to_dict()}


@router.get("/panels/presets", summary="Expert panel presets")
async def get_panel_presets(
    _caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Ready-made expert line-ups; every seat defaults to ``claude``. Providers switched off here are left out."""
    return {"presets": list(PANEL_PRESETS), "providers": list(enabled_panel_providers())}


@router.get("/panels/{thread_id}", summary="Expert panel state")
async def get_panel(
    thread_id: str,
    _caller: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Turns in speaking order, each expert's stance, consensus and the synthesis. 404 if not a panel."""
    result = panel_result(get_conversation_store(), thread_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"panel '{thread_id}' not found")
    return result
