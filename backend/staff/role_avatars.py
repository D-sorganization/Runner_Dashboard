"""Serve role persona avatars: hub custom upload, then RM ``staff/avatars/`` default (#2680)."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from staff.role_avatar_paths import staff_avatars_dir
from staff.roles import RoleSpec

log = logging.getLogger("dashboard.staff.role_avatars")

ROLE_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
AVATAR_DIR_ENV = "RD_AVATAR_DIR"
MAX_AVATAR_BYTES = 2 * 1024 * 1024
MIN_AVATAR_DIM = 64
MAX_AVATAR_DIM = 4096
MAX_AVATAR_ASPECT = 1.25

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


def avatar_upload_dir() -> Path:
    override = os.environ.get(AVATAR_DIR_ENV)
    if override:
        base = Path(override).expanduser()
    else:
        base = Path.home() / ".config" / "runner-dashboard" / "avatars"
    base.mkdir(parents=True, mode=0o755, exist_ok=True)
    return base


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


def delete_custom_avatar(role: str) -> None:
    for path in _custom_avatar_paths(role):
        path.unlink(missing_ok=True)


def store_custom_avatar(role: str, data: bytes, ext: str) -> Path:
    """Atomically store one custom avatar file per role (mode 0600)."""
    delete_custom_avatar(role)
    base = avatar_upload_dir()
    dest = base / f"{role}.{ext}"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{role}.", suffix=f".{ext}.tmp", dir=base)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, dest)
        os.chmod(dest, 0o600)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return dest
