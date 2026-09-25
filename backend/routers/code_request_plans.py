"""Planner stage API for Code Requests (CR-4, #1285).

Thin HTTP layer over ``code_requests.plan_service``: every route needs
``code-requests.manage``; nothing is written to GitHub before approval when the
planner profile's ``plan_requires_approval`` gate is on (the default).
"""

from __future__ import annotations

from typing import Any

from code_requests.dispatch import trigger_workflow_dispatch
from code_requests.plan_service import (
    PlanDeps,
    PlanningError,
    approve_plan,
    edit_draft,
    ingest_plan_comment,
    start_planning,
    submit_plan,
)
from code_requests.plan_store import PlanSessionStore
from dashboard_config import ORG
from fastapi import APIRouter, Depends, HTTPException
from gh_utils import gh_api, gh_api_write
from identity import Principal, require_scope
from pydantic import BaseModel, Field
from routers.code_requests import _get_profile_store, _get_store

router = APIRouter(tags=["code_requests"])

_sessions = PlanSessionStore()
_MANAGE = require_scope("code-requests.manage")


class PlanSubmission(BaseModel):
    output: str = Field(min_length=1, max_length=200_000, description="Planner output containing the JSON plan")


class DraftEdit(BaseModel):
    plan: dict[str, Any] = Field(description="The complete edited plan (same shape as the planner output)")


def _deps() -> PlanDeps:
    return PlanDeps(
        requests=_get_store(),
        profiles=_get_profile_store(),
        sessions=_sessions,
        org=ORG,
        fetch=gh_api,
        write=gh_api_write,
        dispatch=trigger_workflow_dispatch,
    )


def _actor(principal: Principal) -> str:
    return principal.id or principal.name or "operator"


async def _run(call: Any) -> dict[str, Any]:
    try:
        session = await call
    except PlanningError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return session.model_dump(mode="json")


@router.get("/api/code-requests/{id}/plan")
async def get_plan(id: str, _principal: Principal = Depends(_MANAGE)) -> dict[str, Any]:  # noqa: B008
    """The planning session: status, attempts, validator errors and the draft plan."""
    session = _sessions.get(id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"planning has not started for {id!r}")
    return session.model_dump(mode="json")


@router.post("/api/code-requests/{id}/plan/start")
async def start_plan(id: str, principal: Principal = Depends(_MANAGE)) -> dict[str, Any]:  # noqa: B008
    """Dispatch the planner agent for a Code Request in the ``planning`` state."""
    return await _run(start_planning(_deps(), id, principal=_actor(principal)))


@router.post("/api/code-requests/{id}/plan")
async def post_plan(id: str, body: PlanSubmission, principal: Principal = Depends(_MANAGE)) -> dict[str, Any]:  # noqa: B008
    """Submit planner output; it is validated, then drafted, filed, re-prompted or failed."""
    return await _run(submit_plan(_deps(), id, body.output, actor=_actor(principal)))


@router.post("/api/code-requests/{id}/plan/ingest")
async def ingest_plan(id: str, principal: Principal = Depends(_MANAGE)) -> dict[str, Any]:  # noqa: B008
    """Submit the newest ``<!-- plan:v1 -->`` comment on the Code Request issue."""
    return await _run(ingest_plan_comment(_deps(), id, actor=_actor(principal)))


@router.put("/api/code-requests/{id}/plan/draft")
async def put_draft(id: str, body: DraftEdit, _principal: Principal = Depends(_MANAGE)) -> dict[str, Any]:  # noqa: B008
    """Replace the draft with an operator's edits; the edited plan is re-validated."""
    return await _run(edit_draft(_deps(), id, body.plan))


@router.post("/api/code-requests/{id}/plan/approve")
async def approve(id: str, principal: Principal = Depends(_MANAGE)) -> dict[str, Any]:  # noqa: B008
    """Approve the draft: file the epic and children on GitHub and mark the request ``planned``."""
    return await _run(approve_plan(_deps(), id, actor=_actor(principal)))
