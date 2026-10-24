"""Staff role persona avatar GET endpoint (#2680 slice 2)."""

# ruff: noqa: B008
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from identity import Principal, require_scope
from staff.role_avatars import ResolvedAvatar, normalize_role_id, resolve_avatar, roster_avatar_fields
from staff.roles import RoleSpec
from staff.runner import get_runner

router = APIRouter(tags=["staff-avatars"])


def _get_role_spec(role_id: str) -> tuple[str, RoleSpec]:
    normalized = normalize_role_id(role_id)
    if normalized is None:
        raise HTTPException(status_code=404, detail={"error": "role_not_found"})
    roles = get_runner().roles()
    spec = roles.get(normalized)
    if spec is None:
        raise HTTPException(status_code=404, detail={"error": "role_not_found"})
    return normalized, spec


@router.get("/roles/{role_id}/avatar")
async def get_role_avatar(
    role_id: str,
    request: Request,
    _peer: Principal = Depends(require_scope("staff.read")),
) -> Response:
    role, spec = _get_role_spec(role_id)
    resolved = resolve_avatar(role, spec)
    if resolved is None:
        raise HTTPException(status_code=404, detail={"error": "avatar_not_found"})
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match.strip('"') == resolved.etag:
        return Response(status_code=304, headers=_avatar_headers(resolved))
    return FileResponse(
        resolved.path,
        media_type=resolved.content_type,
        headers=_avatar_headers(resolved),
    )


def _avatar_headers(resolved: ResolvedAvatar) -> dict[str, str]:
    return {
        "Cache-Control": "private, max-age=300",
        "ETag": f'"{resolved.etag}"',
        "X-Content-Type-Options": "nosniff",
    }


def enrich_roster_role(name: str, spec: RoleSpec, active_runs: int) -> dict[str, Any]:
    payload = {**spec.to_dict(), "active_runs": active_runs}
    payload.update(roster_avatar_fields(name, spec))
    return payload
