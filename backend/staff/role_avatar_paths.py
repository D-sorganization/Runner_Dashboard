"""RM ``staff/avatars/`` path contract for role YAML ``avatar`` fields (#2680, RM#2410)."""

from __future__ import annotations

import re
from pathlib import Path

from staff.roles import roles_dir

# Relative to Repository_Management ``staff/avatars/`` (e.g. ``barb.webp``, ``personas/marge.webp``).
AVATAR_YML_RE = re.compile(r"^(?:[a-z0-9-]+/)*[a-z0-9-]+\.webp$", re.IGNORECASE)


def staff_avatars_dir_for_roles(roles_directory: Path) -> Path:
    """``staff/avatars/`` beside the ``staff/roles/`` tree being loaded."""
    return roles_directory.parent / "avatars"


def avatars_dir_mtime_sig(avatars_dir: Path) -> tuple[tuple[str, int], ...]:
    """Stable signature of WebP files under ``avatars_dir`` for role-load caching."""
    if not avatars_dir.is_dir():
        return ()
    entries: list[tuple[str, int]] = []
    for path in sorted(avatars_dir.rglob("*.webp")):
        try:
            rel = str(path.relative_to(avatars_dir))
            entries.append((rel, path.stat().st_mtime_ns))
        except (OSError, ValueError):
            continue
    return tuple(entries)


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


def validate_avatar_yaml_path(rel: str, *, avatars_dir: Path | None = None) -> str | None:
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
    avatars = avatars_dir if avatars_dir is not None else staff_avatars_dir()
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
