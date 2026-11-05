"""Fleet hub endpoint for per-run JWT credentials (#3879 phase B).

Mirrors ``routers.fleet_run_token``'s auth shape: a registered fleet node
requests a mint for one of its own runs. Static ``agent-tokens/*.token``
ingestion is unaffected; see docs/architecture/per-run-jwt-credentials.md.
"""

from __future__ import annotations

import dashboard_config
from fastapi import APIRouter, Depends, Header, HTTPException
from identity import identity_manager, require_fleet_peer
from pydantic import BaseModel, Field
from routers.fleet_run_token import require_fleet_run_token_caller
from staff.run_jwt_service import mint_run_jwt

router = APIRouter(tags=["fleet"])


class RunJwtRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=80)
    principal_id: str = Field(min_length=1, max_length=80)


class RunJwtResponse(BaseModel):
    token: str
    expires_at: float
    jti: str


@router.post(
    "/api/fleet/run-jwt",
    response_model=RunJwtResponse,
    dependencies=[Depends(require_fleet_peer), Depends(require_fleet_run_token_caller)],
)
async def post_run_jwt(
    body: RunJwtRequest,
    fleet_node: str | None = Header(default=None, alias="X-Fleet-Node"),
) -> RunJwtResponse:
    """Mint a short-lived, run-scoped JWT for a staff run executing on a spoke node."""
    if dashboard_config.MACHINE_ROLE != "hub":
        raise HTTPException(status_code=404, detail="run-jwt minting is only available on the fleet hub")
    cred = mint_run_jwt(
        identity_manager,
        run_id=body.run_id,
        principal_id=body.principal_id,
        caller_node=fleet_node or "",
    )
    return RunJwtResponse(token=cred.token, expires_at=cred.expires_at, jti=cred.jti)
