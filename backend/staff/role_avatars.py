"""Role persona avatars: default images from RM staff/ and optional per-hub uploads (#2680)."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from staff.roles import RoleSpec, roles_dir

log = logging.getLogger("dashboard.staff.role_avatars")

ROLE_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
# Relative to Repository_Management ``staff/avatars/`` (RM#2410 / RD#2680).
AVATAR_YML_RE = re.compile(r"^(?:[a-z0-9-]+/)*[a-z0-9-]+\.webp$", re.IGNORECASE)
AVATAR_DIR_ENV = "RD_AVATAR_DIR"
MAX_AVATAR_BYTES = 2 * 1024 * 1024
MIN_AVATAR_DIM = 64
MAX_AVATAR_DIM = 4096
MAX_AVATAR_ASPECT = 1.25

AvatarSource = Literal["custom", "default", "none"]

_CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


@dataclass(frozen=True)
class ResolvedAvatar:
    path: Path
    source: Literal["custom", "default"]
    etag: str
    content_type: str
    width: int | None = None
    height: int | None = None
    byte_size: int | None = None


def staff_root() -> Path | None:
    """Parent of the roles directory (Repository_Management ``staff/``)."""
    rdir = roles_dir()
    if rdir is None:
        return None
    return rdir.parent


def staff_avatars_dir() -> Path | None:
    """Default persona images published beside ``staff/roles/`` (``staff/avatars/``)."""
    root = staff_root()
    if root is None:
        return None
    return root / "avatars"


def avatar_upload_dir() -> Path:
    override = os.environ.get(AVATAR_DIR_ENV)
    if override:
        base = Path(override).expanduser()
    else:
        base = Path.home() / ".config" / "runner-dashboard" / "avatars"
    base.mkdir(parents=True, mode=0o755, exist_ok=True)
    return base


def validate_avatar_yaml_path(rel: str) -> str | None:
    """Return an error string when ``rel`` is not a safe path under ``staff/avatars/``."""
    if not isinstance(rel, str) or not rel.strip():
        return "avatar must be a non-empty string"
    rel = rel.strip().replace("\\", "/")
    if rel.startswith("/"):
        return f"avatar {rel!r} must be relative to staff/avatars/"
    parts = Path(rel).parts
    if ".." in parts:
        return f"avatar {rel!r} must not contain '..'"
    if not AVATAR_YML_RE.match(rel):
        return f"avatar {rel!r} must match {AVATAR_YML_RE.pattern}"
    avatars = staff_avatars_dir()
    if avatars is None:
        return None
    try:
        resolved = (avatars / rel).resolve()
        resolved.relative_to(avatars.resolve())
    except ValueError:
        return f"avatar {rel!r} escapes staff/avatars/"
    if not resolved.is_file():
        return f"avatar file not found: {rel}"
    return None


def resolve_default_avatar_path(spec: RoleSpec) -> Path | None:
    if not spec.avatar or not spec.valid:
        return None
    avatars = staff_avatars_dir()
    if avatars is None:
        return None
    rel = spec.avatar.strip().replace("\\", "/")
    try:
        resolved = (avatars / rel).resolve()
        resolved.relative_to(avatars.resolve())
    except ValueError:
        return None
    return resolved if resolved.is_file() else None


def _custom_avatar_paths(role: str) -> list[Path]:
    base = avatar_upload_dir()
    return [base / f"{role}.{ext}" for ext in ("png", "jpg", "jpeg", "webp")]


def find_custom_avatar(role: str) -> Path | None:
    for path in _custom_avatar_paths(role):
        if path.is_file():
            return path
    return None


def _etag_for_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_type_for_path(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    return _CONTENT_TYPES.get(ext, "application/octet-stream")


def resolve_avatar(role: str, spec: RoleSpec | None) -> ResolvedAvatar | None:
    """Custom upload, then yml default, then None."""
    if spec is None or not spec.valid:
        return None
    custom = find_custom_avatar(role)
    if custom is not None:
        return ResolvedAvatar(
            path=custom,
            source="custom",
            etag=_etag_for_file(custom),
            content_type=_content_type_for_path(custom),
            byte_size=custom.stat().st_size,
        )
    default = resolve_default_avatar_path(spec)
    if default is not None:
        return ResolvedAvatar(
            path=default,
            source="default",
            etag=_etag_for_file(default),
            content_type=_content_type_for_path(default),
            byte_size=default.stat().st_size,
        )
    return None


def roster_avatar_fields(role: str, spec: RoleSpec) -> dict[str, str | None]:
    resolved = resolve_avatar(role, spec)
    if resolved is None:
        return {"avatar_url": None, "avatar_source": "none"}
    return {
        "avatar_url": f"/api/v1/staff/roles/{role}/avatar?v={resolved.etag}",
        "avatar_source": resolved.source,
    }


def normalize_role_id(role: str) -> str | None:
    role = role.strip()
    if not ROLE_ID_RE.fullmatch(role):
        return None
    return role
