"""Fleet hub endpoint for spoke staff-run GitHub tokens (#2051)."""

from __future__ import annotations

import hmac
import os

import dashboard_config
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from identity import Principal, _resolve_principal_optional, auth_header, principal_has_scope, require_fleet_peer
from pydantic import BaseModel, Field
from staff.fleet_run_mint_policy import (
    FLEET_RUN_CREDENTIAL_MINT_SCOPE,
    assert_mint_caller_bound_to_run,
    audit_mint,
    lease_principal_id_for_run,
    load_active_run_for_mint,
    repositories_for_run_mint,
    role_spec_for_run,
)
from staff.fleet_run_token_service import mint_fleet_run_github_token

router = APIRouter(tags=["fleet"])


class FleetRunTokenRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=80)


class FleetRunTokenResponse(BaseModel):
    token: str
    expires_at: float


def require_fleet_run_token_caller(
    request: Request,
    header_token: str | None = Depends(auth_header),
) -> Principal:
    """Authenticate a mint caller and require ``fleet.run_credential.mint`` (fail closed)."""
    hub_token = os.environ.get("HUB_FLEET_TOKEN", "").strip()
    if not hub_token:
        raise HTTPException(status_code=503, detail="HUB_FLEET_TOKEN is not configured on the hub")
    principal = _resolve_principal_optional(request, header_token)
    if principal is None and header_token and header_token.startswith("Bearer "):
        presented = header_token[7:]
        if hmac.compare_digest(presented, hub_token):
            principal = Principal(id="fleet-peer", type="bot", name="Fleet Peer", roles=["fleet-peer"])
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not principal_has_scope(principal, FLEET_RUN_CREDENTIAL_MINT_SCOPE):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "Authorization failed",
                "required_scope": FLEET_RUN_CREDENTIAL_MINT_SCOPE,
                "principal": principal.id,
            },
        )
    return principal


@router.post(
    "/api/fleet/run-token",
    response_model=FleetRunTokenResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_fleet_peer)],
)
async def post_fleet_run_token(
    body: FleetRunTokenRequest,
    caller: Principal = Depends(require_fleet_run_token_caller),  # noqa: B008
    fleet_node: str | None = Header(default=None, alias="X-Fleet-Node"),
) -> FleetRunTokenResponse:
    """Mint a short-lived GitHub App token for a staff run executing on a spoke node."""
    if dashboard_config.MACHINE_ROLE != "hub":
        raise HTTPException(status_code=404, detail="run-token minting is only available on the fleet hub")
    rec = load_active_run_for_mint(body.run_id)
    lease_principal = lease_principal_id_for_run(rec)
    assert_mint_caller_bound_to_run(caller, run_id=rec.id, role=rec.role, lease_principal=lease_principal)
    role = role_spec_for_run(rec)
    repositories = repositories_for_run_mint(role, rec.repo)
    cred = mint_fleet_run_github_token(
        run_id=rec.id,
        role_name=role.name,
        repositories=repositories,
        caller_node=fleet_node or "",
    )
    audit_mint(kind="github", run_id=rec.id, principal_id=lease_principal, caller_id=caller.id)
    return FleetRunTokenResponse(token=cred.token, expires_at=cred.expires_at)
