from __future__ import annotations

import hmac
import os
from typing import Any

from escalation.cap import capacity_refused, resolve_cap
from escalation.merge_jump import (
    enqueue_jump,
    plan_from_pr_view,
    refusal_from_pr_payload,
    split_repo,
)
from escalation.store import EscalationEvent, EscalationOutcome, get_escalation_store, reset_escalation_store
from fastapi import APIRouter, Depends, HTTPException, Request
from gh_utils import gh_api
from identity import Principal, _resolve_principal_optional, auth_header, require_fleet_peer, require_scope
from pydantic import BaseModel, Field
from staff.audit import record_audit

router = APIRouter(tags=["escalation"])

_STAFF_READ = require_scope("staff.read")
_STAFF_ADMIN = require_scope("staff.admin")


class EscalationIngestBody(BaseModel):
    repo: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
    pr_number: int = Field(gt=0)
    github_actor: str = Field(min_length=1, max_length=120)
    outcome: EscalationOutcome
    reason: str = Field(default="", max_length=2000)
    concurrent_cap: int | None = Field(default=None, ge=1, le=50)


class StaffEscalateBody(BaseModel):
    repo: str = Field(min_length=1, max_length=200)
    pr: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=2000)


def require_escalation_ingest_caller(
    request: Request,
    header_token: str | None = Depends(auth_header),
) -> Principal:
    """Fleet nodes and GitHub workflows authenticate with ``HUB_FLEET_TOKEN`` (fail closed)."""
    hub_token = os.environ.get("HUB_FLEET_TOKEN", "").strip()
    if not hub_token:
        raise HTTPException(status_code=503, detail="HUB_FLEET_TOKEN is not configured on the hub")
    principal = _resolve_principal_optional(request, header_token)
    if principal is not None:
        return principal
    if header_token and header_token.startswith("Bearer "):
        presented = header_token[7:]
        if hmac.compare_digest(presented, hub_token):
            return Principal(id="fleet-ingest", type="bot", name="Fleet Ingest", roles=["fleet-peer"])
    raise HTTPException(status_code=401, detail="Authentication required")


@router.post(
    "/api/fleet/escalation-events",
    dependencies=[Depends(require_fleet_peer), Depends(require_escalation_ingest_caller)],
)
async def ingest_escalation_event(body: EscalationIngestBody) -> dict[str, Any]:
    """Record a label guard outcome from a fleet repository workflow."""
    store = get_escalation_store()
    if body.outcome == "applied":
        cap = resolve_cap(body.concurrent_cap)
        refused = capacity_refused(body.repo, cap, body.pr_number, store)
        if refused:
            raise HTTPException(status_code=409, detail={"code": "expedite_cap", "message": refused})
    row_id = store.append(
        EscalationEvent(
            repo=body.repo,
            pr_number=body.pr_number,
            github_actor=body.github_actor,
            outcome=body.outcome,
            reason=body.reason,
        )
    )
    return {"recorded": True, "id": row_id}


@router.get("/api/staff/escalations")
async def list_escalations(
    request: Request,
    _caller: Principal = Depends(_STAFF_READ),  # noqa: B008
) -> dict[str, Any]:
    """Active expedited PRs (latest hub event ``applied``) and recent audit rows."""
    try:
        limit = min(int(request.query_params.get("limit", "50")), 200)
    except ValueError:
        limit = 50
    store = get_escalation_store()
    return {
        "active": store.list_active(),
        "recent": store.list_recent(limit=limit),
        "label": "priority:expedite",
    }


@router.post("/api/staff/escalate")
async def staff_escalate_merge_queue(
    body: StaffEscalateBody,
    caller: Principal = Depends(_STAFF_ADMIN),  # noqa: B008
) -> dict[str, Any]:
    """Jump a green, unheld PR to the front of the merge queue (never skips required checks)."""
    owner, repo = split_repo(body.repo)
    slug = f"{owner}/{repo}"
    pr_payload = await gh_api(f"repos/{owner}/{repo}/pulls/{body.pr}")
    refusal = refusal_from_pr_payload(pr_payload, repo)
    if refusal:
        raise HTTPException(status_code=409, detail={"code": "escalate_refused", "message": refusal})
    try:
        plan = plan_from_pr_view(pr_payload, owner, repo, body.pr)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    result = await enqueue_jump(plan)
    record_audit(
        action="maintenance",
        target=f"escalate:{slug}#{body.pr}",
        principal=caller.id,
        detail={"reason": body.reason, "ok": result.ok, "message": result.message},
        surface="api",
    )
    if result.ok:
        get_escalation_store().append(
            EscalationEvent(
                repo=slug,
                pr_number=body.pr,
                github_actor=caller.id,
                outcome="applied",
                reason=f"merge_jump: {body.reason}",
            )
        )
    if result.ok:
        return {"status": "enqueued", "message": result.message, "merge_queue_entry": result.detail}
    if result.jump_ignored:
        response: dict[str, Any] = {
            "status": "jump_ignored",
            "message": result.message,
            "merge_queue_entry": result.detail,
        }
        if result.manual_command:
            response["gh_personal_command"] = result.manual_command
        return response
    if result.manual_command:
        return {
            "status": "manual_required",
            "message": result.message,
            "gh_personal_command": result.manual_command,
        }
    raise HTTPException(status_code=502, detail=result.message)


__all__ = ["router", "reset_escalation_store"]
