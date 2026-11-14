"""Persona avatar image serving and custom upload/reset for Staff API (#2680, #3500)."""

# ruff: noqa: B008
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from identity import Principal, require_scope
from staff.personal_avatars import (
    MAX_AVATAR_BYTES,
    AvatarValidationError,
    get_personal_avatar_store,
    validate_safe_id,
)
from staff.presentation_prefs import (
    PreferencesConflictError,
    PreferencesValidationError,
    PresentationPreferences,
    get_presentation_prefs_store,
)
from staff.role_avatar_paths import staff_avatars_dir, validate_avatar_yaml_path
from staff.roles import load_roles

router = APIRouter(tags=["staff-avatars"])


async def _read_bounded_body(request: Request) -> bytes:
    limit_err = HTTPException(413, f"payload exceeds limit of {MAX_AVATAR_BYTES} bytes")
    cl = request.headers.get("content-length")
    if cl and int(cl) > MAX_AVATAR_BYTES:
        raise limit_err
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_AVATAR_BYTES:
            raise limit_err
        chunks.append(chunk)
    return b"".join(chunks)


def _check_role_and_rev(
    role_id: str, op_id: str, exp_rev: int | None
) -> tuple[frozenset[str], PresentationPreferences]:
    try:
        validate_safe_id(role_id, "role_id")
    except AvatarValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    known = frozenset(load_roles().keys())
    if role_id not in known:
        raise HTTPException(422, f"unknown role id: {role_id!r}")
    prefs = get_presentation_prefs_store().get(op_id)
    if exp_rev is not None and exp_rev != prefs.revision:
        raise HTTPException(409, f"stale revision; current revision is {prefs.revision}")
    return known, prefs


def _save_override(
    op_id: str,
    prefs: PresentationPreferences,
    role_id: str,
    url: str | None,
    known: frozenset[str],
) -> None:
    overrides = (
        {**prefs.avatar_overrides, role_id: url}
        if url
        else {k: v for k, v in prefs.avatar_overrides.items() if k != role_id}
    )
    try:
        get_presentation_prefs_store().save(
            op_id,
            expected_revision=prefs.revision,
            display_label=prefs.display_label,
            role_order=prefs.role_order,
            avatar_overrides=overrides,
            known_role_ids=known,
        )
    except (PreferencesConflictError, PreferencesValidationError) as exc:
        code = 409 if isinstance(exc, PreferencesConflictError) else 422
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.put("/avatars/custom/{role_id}", include_in_schema=False)
async def upload_custom_avatar_v1(
    role_id: str,
    request: Request,
    expected_revision: int | None = Query(default=None),
    peer: Principal = Depends(require_scope("staff.write")),
) -> dict[str, Any]:
    known, prefs = _check_role_and_rev(role_id, peer.id, expected_revision)
    body = await _read_bounded_body(request)
    try:
        _p, _m, h = get_personal_avatar_store().save_avatar(peer.id, role_id, body)
    except AvatarValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    url = f"/api/v1/staff/avatars/personal/{peer.id}/{role_id}?v={h}"
    _save_override(peer.id, prefs, role_id, url, known)
    return {"ok": True, "operator_id": peer.id, "role_id": role_id, "avatar_url": url}


@router.post("/avatars/custom/{role_id}/reset", include_in_schema=False)
async def reset_custom_avatar_v1(
    role_id: str,
    expected_revision: int | None = Query(default=None),
    peer: Principal = Depends(require_scope("staff.write")),
) -> dict[str, Any]:
    known, prefs = _check_role_and_rev(role_id, peer.id, expected_revision)
    _save_override(peer.id, prefs, role_id, None, known)
    get_personal_avatar_store().reset_avatar(peer.id, role_id)
    return {"ok": True, "role_id": role_id, "reset": True}


@router.get("/avatars/personal/{operator_id}/{role_id}", include_in_schema=False)
async def get_personal_avatar_v1(
    operator_id: str,
    role_id: str,
    peer: Principal = Depends(require_scope("staff.read")),
) -> FileResponse:
    if peer.id != operator_id and "staff.admin" not in peer.scopes and "*" not in peer.scopes:
        raise HTTPException(status_code=403, detail="cross-operator avatar access forbidden")
    try:
        validate_safe_id(operator_id, "operator_id")
        validate_safe_id(role_id, "role_id")
    except AvatarValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    info = get_personal_avatar_store().get_avatar(operator_id, role_id)
    if info:
        return FileResponse(
            info[0],
            media_type=info[1],
            headers={"Cache-Control": "private, max-age=3600"},
        )
    avatars_dir = staff_avatars_dir()
    fallback = f"{role_id}.webp"
    if avatars_dir and not validate_avatar_yaml_path(fallback, avatars_dir=avatars_dir):
        return FileResponse(avatars_dir / fallback, media_type="image/webp")
    raise HTTPException(status_code=404, detail="avatar not found")


@router.get("/avatars/{avatar_rel:path}")
async def avatar_v1(
    avatar_rel: str,
    _peer: Principal = Depends(require_scope("staff.read")),
) -> FileResponse:
    """Serve a role's persona image from RM ``staff/avatars/`` (#2680)."""
    avatars_dir = staff_avatars_dir()
    if avatars_dir is None or validate_avatar_yaml_path(avatar_rel, avatars_dir=avatars_dir):
        raise HTTPException(status_code=404, detail="avatar not found")
    return FileResponse(avatars_dir / avatar_rel, media_type="image/webp")
