"""Fleet hub endpoint for per-run JWT credentials (#3879 phase B).

Mirrors ``routers.fleet_run_token``'s auth shape: a registered fleet node
requests a mint for one of its own runs. Static ``agent-tokens/*.token``
ingestion is unaffected; see docs/architecture/per-run-jwt-credentials.md.
"""

from __future__ import annotations

import dashboard_config
from fastapi import APIRouter, Depends, Header, HTTPException
from identity import Principal, identity_manager, require_fleet_peer
from pydantic import BaseModel, Field
from routers.fleet_run_token import require_fleet_run_token_caller
from staff.fleet_run_mint_policy import (
    assert_mint_caller_bound_to_run,
    audit_mint,
    lease_principal_id_for_run,
    load_active_run_for_mint,
    principal_mint_scope_denied,
)
from staff.run_jwt_service import mint_run_jwt

router = APIRouter(tags=["fleet"])


class RunJwtRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=80)


class RunJwtResponse(BaseModel):
    token: str
    expires_at: float
    jti: str


@router.post(
    "/api/fleet/run-jwt",
    response_model=RunJwtResponse,
    dependencies=[Depends(require_fleet_peer)],
)
async def post_run_jwt(
    body: RunJwtRequest,
    caller: Principal = Depends(require_fleet_run_token_caller),  # noqa: B008
    fleet_node: str | None = Header(default=None, alias="X-Fleet-Node"),
) -> RunJwtResponse:
    """Mint a short-lived, run-scoped JWT for a staff run executing on a spoke node."""
    if dashboard_config.MACHINE_ROLE != "hub":
        raise HTTPException(status_code=404, detail="run-jwt minting is only available on the fleet hub")
    rec = load_active_run_for_mint(body.run_id)
    lease_principal = lease_principal_id_for_run(rec)
    assert_mint_caller_bound_to_run(caller, run_id=rec.id, role=rec.role, lease_principal=lease_principal)
    target = identity_manager.get_principal(lease_principal)
    if target is None:
        raise HTTPException(status_code=422, detail=f"unknown principal {lease_principal!r}")
    if principal_mint_scope_denied(target):
        raise HTTPException(status_code=403, detail="principal cannot receive run JWT")
    cred = mint_run_jwt(
        identity_manager,
        run_id=rec.id,
        principal_id=lease_principal,
        caller_node=fleet_node or "",
    )
    audit_mint(kind="jwt", run_id=rec.id, principal_id=lease_principal, caller_id=caller.id)
    return RunJwtResponse(token=cred.token, expires_at=cred.expires_at, jti=cred.jti)
