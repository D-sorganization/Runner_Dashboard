"""Narrow active-run readers; source management and intake keep their own gates."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request
from identity import (
    Principal,
    _principal_may_read_fleet,
    _resolve_principal_optional,
    auth_cookie,
    auth_header,
    principal_has_scope,
    require_scope,
)
from library.run_reference_recording import requested_run_id
from staff.tokens import active_run_id_for_principal


def requires_shared_content(principal: Principal) -> bool:
    """Peer transport and a library-only reader cannot select private content."""
    return principal.id == "fleet-peer" or (
        principal_has_scope(principal, "library.read") and not _principal_may_read_fleet(principal)
    )


def _active_library_reader(request: Request, header_token: str | None) -> Principal | None:
    principal = _resolve_principal_optional(request, header_token)
    if principal is None or not principal_has_scope(principal, "library.read"):
        return None
    if _principal_may_read_fleet(principal):
        return None
    run_id = active_run_id_for_principal(principal.id)
    if run_id is None or {r for r in principal.roles if r.startswith("staff-run:")} != {f"staff-run:{run_id}"}:
        raise HTTPException(status_code=403, detail="library read requires an active run token")
    requested_run_id(request, principal, body_run_id=request.path_params.get("run_id"))
    return principal


def require_library_reader(
    request: Request,
    header_token: str | None = Depends(auth_header),
    cookie_token: str | None = Depends(auth_cookie),
) -> Principal:
    """Authorize an exact active library reader or preserve existing peer access."""
    principal = _active_library_reader(request, header_token)
    if principal is not None:
        return principal
    from routers.library import require_library_peer

    override = getattr(request.app, "dependency_overrides", {}).get(require_library_peer)
    if override is not None:
        return override() if callable(override) else override
    return require_library_peer(request, header_token, cookie_token)


def require_run_reference_reader(
    request: Request,
    header_token: str | None = Depends(auth_header),
    cookie_token: str | None = Depends(auth_cookie),
) -> Principal:
    """Library capability reads only its active run; ordinary staff ownership stays."""
    principal = _active_library_reader(request, header_token)
    if principal is not None:
        return principal
    principal = require_scope("staff.read")(request, header_token, cookie_token)
    if any(role.startswith("staff-run:") for role in principal.roles):
        requested_run_id(request, principal, body_run_id=request.path_params.get("run_id"))
    return principal


def library_reader_passages(principal: Principal, passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Do not expose local file names through the narrow run reader capability."""
    if principal_has_scope(principal, "library.read") and not _principal_may_read_fleet(principal):
        return [{key: value for key, value in passage.items() if key != "file_path"} for passage in passages]
    return passages
