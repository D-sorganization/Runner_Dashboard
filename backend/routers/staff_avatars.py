"""Persona avatar image serving for the Staff API (#2680)."""

# ruff: noqa: B008
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from identity import Principal, require_scope
from staff.role_avatar_paths import staff_avatars_dir, validate_avatar_yaml_path

router = APIRouter(tags=["staff-avatars"])


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
