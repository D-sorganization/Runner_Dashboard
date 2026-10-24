"""Staff role persona avatar endpoints (#2680)."""

# ruff: noqa: B008
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from identity import Principal, require_scope
from staff.role_avatar_image import AvatarValidationError, validate_avatar_upload
from staff.role_avatars import (
    MAX_AVATAR_BYTES,
    ResolvedAvatar,
    delete_custom_avatar,
    normalize_role_id,
    resolve_avatar,
    roster_avatar_fields,
    store_custom_avatar,
)
from staff.roles import RoleSpec
from staff.runner import get_runner

log = logging.getLogger("dashboard.staff.avatars")

router = APIRouter(tags=["staff-avatars"])


def _require_human_operator(caller: Principal = Depends(require_scope("staff.holds.write"))) -> Principal:
    if caller.type == "bot" or caller.id.startswith("agent-"):
        raise HTTPException(status_code=403, detail={"error": "operator_required"})
    return caller


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


@router.put("/roles/{role_id}/avatar")
async def put_role_avatar(
    role_id: str,
    request: Request,
    caller: Principal = Depends(_require_human_operator),
) -> dict[str, Any]:
    role, spec = _get_role_spec(role_id)
    raw_len = request.headers.get("content-length")
    if raw_len is not None:
        try:
            if int(raw_len) > MAX_AVATAR_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail={"code": "payload_too_large", "message": "Avatar exceeds 2MB limit."},
                )
        except ValueError:
            pass
    body = await request.body()
    if len(body) > MAX_AVATAR_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"code": "payload_too_large", "message": "Avatar exceeds 2MB limit."},
        )
    try:
        parsed = validate_avatar_upload(body)
    except AvatarValidationError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    path = store_custom_avatar(role, body, parsed.ext)
    resolved = resolve_avatar(role, spec)
    if resolved is None:
        raise HTTPException(status_code=500, detail={"error": "avatar_store_failed"})
    log.info("staff: avatar uploaded for %s by %s (%dx%d)", role, caller.id, parsed.width, parsed.height)
    return {
        "role": role,
        "source": "custom",
        "etag": resolved.etag,
        "width": parsed.width,
        "height": parsed.height,
        "bytes": path.stat().st_size,
    }


@router.delete("/roles/{role_id}/avatar", status_code=204)
async def delete_role_avatar(
    role_id: str,
    _caller: Principal = Depends(_require_human_operator),
) -> Response:
    role, _spec = _get_role_spec(role_id)
    delete_custom_avatar(role)
    return Response(status_code=204)


def enrich_roster_role(name: str, spec: RoleSpec, active_runs: int) -> dict[str, Any]:
    payload = {**spec.to_dict(), "active_runs": active_runs}
    payload.update(roster_avatar_fields(name, spec))
    return payload
