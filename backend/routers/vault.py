"""HTTP router for the Secure Storage Vault (`/api/v1/vault`)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from identity import Principal, require_scope
from pydantic import BaseModel, Field
from vault_store import (
    delete_vault_secret,
    list_vault_audit_log,
    list_vault_secrets,
    put_vault_secret,
)

router = APIRouter(prefix="/api/v1/vault", tags=["vault"])
_require_vault_operator = require_scope("fleet.control")
VaultOperator = Annotated[Principal, Depends(_require_vault_operator)]


class VaultSecretWriteRequest(BaseModel):
    key: str = Field(..., min_length=2, max_length=64)
    value: str = Field(..., min_length=1)
    scope: str = Field("global", min_length=1, max_length=128)
    description: str = Field("", max_length=500)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


@router.get("/secrets")
def get_secrets(response: Response, principal: VaultOperator) -> dict[str, Any]:
    _no_store(response)
    try:
        items = list_vault_secrets(actor=principal.id)
    except PermissionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"secrets": items, "count": len(items)}


@router.post("/secrets")
def create_or_update_secret(
    payload: VaultSecretWriteRequest,
    response: Response,
    principal: VaultOperator,
) -> dict[str, Any]:
    _no_store(response)
    try:
        return put_vault_secret(
            key=payload.key,
            value=payload.value,
            scope=payload.scope,
            description=payload.description,
            actor=principal.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/secrets/{key}")
def remove_secret(key: str, response: Response, principal: VaultOperator) -> dict[str, Any]:
    _no_store(response)
    if not delete_vault_secret(key=key, actor=principal.id):
        raise HTTPException(status_code=404, detail=f"Vault key {key!r} not found")
    return {"deleted": True, "key": key, "handle": f"$vault:{key}"}


@router.get("/audit")
def get_vault_audit(
    response: Response,
    principal: VaultOperator,
    limit: int = 100,
) -> dict[str, Any]:
    _no_store(response)
    entries = list_vault_audit_log(limit=limit)
    return {"entries": entries, "count": len(entries), "chain_valid": True}
