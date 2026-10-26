from __future__ import annotations

import hmac
import os
from typing import Any

from escalation.store import EscalationEvent, EscalationOutcome, get_escalation_store, reset_escalation_store
from fastapi import APIRouter, Depends, HTTPException, Request
from identity import Principal, _resolve_principal_optional, auth_header, require_fleet_peer, require_scope
from pydantic import BaseModel, Field

router = APIRouter(tags=["escalation"])

_STAFF_READ = require_scope("staff.read")


class EscalationIngestBody(BaseModel):
    repo: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
    pr_number: int = Field(gt=0)
    github_actor: str = Field(min_length=1, max_length=120)
    outcome: EscalationOutcome
    reason: str = Field(default="", max_length=2000)


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


__all__ = ["router", "reset_escalation_store"]
