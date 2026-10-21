"""Fleet hub endpoint for spoke staff-run GitHub tokens (#2051)."""

from __future__ import annotations

import hmac
import os

import dashboard_config
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from identity import Principal, _resolve_principal_optional, auth_header, require_fleet_peer
from pydantic import BaseModel, Field
from staff.fleet_run_token_service import mint_fleet_run_github_token

router = APIRouter(tags=["fleet"])


class FleetRunTokenRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=80)
    repositories: list[str] = Field(min_length=1, max_length=8)


class FleetRunTokenResponse(BaseModel):
    token: str
    expires_at: float


def require_fleet_run_token_caller(
    request: Request,
    header_token: str | None = Depends(auth_header),
) -> Principal:
    """Authenticate a registered fleet node via ``HUB_FLEET_TOKEN`` (fail closed)."""
    hub_token = os.environ.get("HUB_FLEET_TOKEN", "").strip()
    if not hub_token:
        raise HTTPException(status_code=503, detail="HUB_FLEET_TOKEN is not configured on the hub")
    principal = _resolve_principal_optional(request, header_token)
    if principal is not None:
        return principal
    if header_token and header_token.startswith("Bearer "):
        presented = header_token[7:]
        if hmac.compare_digest(presented, hub_token):
            return Principal(id="fleet-peer", type="bot", name="Fleet Peer", roles=["fleet-peer"])
    raise HTTPException(status_code=401, detail="Authentication required")


@router.post(
    "/api/fleet/run-token",
    response_model=FleetRunTokenResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_fleet_peer), Depends(require_fleet_run_token_caller)],
)
async def post_fleet_run_token(
    body: FleetRunTokenRequest,
    fleet_node: str | None = Header(default=None, alias="X-Fleet-Node"),
) -> FleetRunTokenResponse:
    """Mint a short-lived GitHub App token for a staff run executing on a spoke node."""
    if dashboard_config.MACHINE_ROLE != "hub":
        raise HTTPException(status_code=404, detail="run-token minting is only available on the fleet hub")
    cred = mint_fleet_run_github_token(
        run_id=body.run_id,
        role_name=body.role,
        repositories=body.repositories,
        caller_node=fleet_node or "",
    )
    return FleetRunTokenResponse(token=cred.token, expires_at=cred.expires_at)
