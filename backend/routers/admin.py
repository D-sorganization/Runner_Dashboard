from fastapi import APIRouter, Depends, HTTPException
from identity import Principal, identity_manager, require_scope
from pydantic import BaseModel

router = APIRouter(prefix="/api/admin", tags=["admin"])


class TokenCreateRequest(BaseModel):
    name: str
    expires_in_days: int | None = None


class QuotaUpdateRequest(BaseModel):
    max_runners: int | None = None
    agent_spend_usd_day: float | None = None
    local_app_slots: int | None = None


@router.get("/principals")
def list_principals(_auth: Principal = Depends(require_scope("admin"))):  # noqa: B008
    """List all registered principals and their quotas."""
    return {"principals": [p.model_dump() for p in identity_manager.principals.values()]}


@router.get("/tokens")
def list_tokens(_auth: Principal = Depends(require_scope("admin"))):  # noqa: B008
    """List all active service tokens (hashes only)."""
    return {"tokens": [t.model_dump() for t in identity_manager.tokens]}


@router.get("/token-health", dependencies=[Depends(require_scope("admin"))])
def get_token_health():
    """Agent token presence, validity and days to expiry per configured principal (#3895)."""
    from agent_token_health import token_health_report  # noqa: PLC0415

    return {"principals": token_health_report(identity_manager)}


@router.post("/principals/{principal_id}/token")
def create_service_token(
    principal_id: str,
    req: TokenCreateRequest,
    _auth: Principal = Depends(require_scope("admin")),  # noqa: B008
):
    """Mint a new service token for a bot principal."""
    try:
        raw_token = identity_manager.mint_service_token(
            principal_id,
            name=req.name,
            expires_in_days=req.expires_in_days,
        )
        return {"principal_id": principal_id, "token": raw_token}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/tokens/{token_hash}")
def revoke_service_token(token_hash: str, _auth: Principal = Depends(require_scope("admin"))):  # noqa: B008
    """Revoke a service token by its hash."""
    identity_manager.revoke_token(token_hash)
    return {"success": True}


@router.patch("/principals/{principal_id}/quota")
def update_principal_quota(
    principal_id: str,
    req: QuotaUpdateRequest,
    _auth: Principal = Depends(require_scope("admin")),  # noqa: B008
):
    """Update quotas for a specific principal."""
    prin = identity_manager.get_principal(principal_id)
    if not prin:
        raise HTTPException(status_code=404, detail="Principal not found")

    if req.max_runners is not None:
        prin.quotas.max_runners = req.max_runners
    if req.agent_spend_usd_day is not None:
        prin.quotas.agent_spend_usd_day = req.agent_spend_usd_day
    if req.local_app_slots is not None:
        prin.quotas.local_app_slots = req.local_app_slots

    # Save to principals.yml
    import yaml

    with open(identity_manager.principals_path, "w") as f:
        yaml.dump(
            {"principals": [p.model_dump() for p in identity_manager.principals.values()]},
            f,
        )

    return prin.model_dump()
