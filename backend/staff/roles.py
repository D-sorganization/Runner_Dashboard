"""Staff role registry — reads ``staff/roles/*.yml`` published by Repository_Management.

The dashboard never imports Repository_Management code. It locates the role
directory on disk (operator override → sibling checkout → fleet-sync copy)
and parses the YAML with the same shape that
``Repository_Management/shared_scripts/staff_roles.py`` validates.

A small built-in fallback roster keeps the API usable on a node that has no
RM checkout (e.g. a fresh laptop), so the Staff tab renders instead of 503ing.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

from staff.role_spec import RoleSpec, parse_role, read_and_parse_role_file

log = logging.getLogger("dashboard.staff.roles")

ROLES_DIR_ENV = "STAFF_ROLES_DIR"
SCHEMA_PATH_ENV = "STAFF_SCHEMA_PATH"
_SIBLING_RELATIVE = ("Repository_Management", "staff", "roles")
_SIBLING_SCHEMA_RELATIVE = ("Repository_Management", "staff", "schema.json")
_LOCAL_SCHEMA_PATH = Path(__file__).parent / "schema.json"

__all__ = [
    "AD_HOC_ROLE",
    "RoleSpec",
    "clear_roles_cache",
    "load_roles",
    "parse_role",
    "role_validation_errors",
    "roles_dir",
    "schema_path",
]


def roles_dir() -> Path | None:
    """Resolve the role directory. Returns None when nothing is found."""
    override = os.environ.get(ROLES_DIR_ENV)
    if override:
        p = Path(override).expanduser()
        return p if p.is_dir() else None
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    for parent in (here.parents[2], here.parents[3]) if len(here.parents) > 3 else (here.parents[2],):
        candidates.append(parent.parent.joinpath(*_SIBLING_RELATIVE))
        candidates.append(parent.joinpath(*_SIBLING_RELATIVE))
    home = Path.home()
    candidates.append(home / "Repositories" / "Repository_Management" / "staff" / "roles")
    candidates.append(home / "Repositories" / "Repository_Management-worktrees" / "main" / "staff" / "roles")
    candidates.append(home / "actions-runners" / "Repository_Management" / "staff" / "roles")
    candidates.append(home / ".config" / "runner-dashboard" / "staff" / "roles")
    for c in candidates:
        if c.is_dir():
            return c
    return None


def schema_path() -> Path:
    """Resolve path to staff schema.json."""
    override = os.environ.get(SCHEMA_PATH_ENV)
    if override:
        p = Path(override).expanduser()
        if p.is_file():
            return p
    r_dir = roles_dir()
    if r_dir is not None:
        parent_schema = r_dir.parent / "schema.json"
        if parent_schema.is_file():
            return parent_schema
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    for parent in (here.parents[2], here.parents[3]) if len(here.parents) > 3 else (here.parents[2],):
        candidates.append(parent.parent.joinpath(*_SIBLING_SCHEMA_RELATIVE))
        candidates.append(parent.joinpath(*_SIBLING_SCHEMA_RELATIVE))
    home = Path.home()
    candidates.append(home / "Repositories" / "Repository_Management" / "staff" / "schema.json")
    candidates.append(home / "actions-runners" / "Repository_Management" / "staff" / "schema.json")
    candidates.append(_LOCAL_SCHEMA_PATH)
    for c in candidates:
        if c.is_file():
            return c
    return _LOCAL_SCHEMA_PATH


# The built-in free-form role; with no repository it has nothing to open a PR against.
AD_HOC_ROLE = "ad-hoc"

_FALLBACK_ROLES: tuple[dict[str, Any], ...] = (
    {
        "name": AD_HOC_ROLE,
        "title": "Ad-hoc task",
        "summary": "Free-form coding task dispatched by an operator.",
        "providers": ["claude", "codex", "antigravity"],
        "permissions": {
            "lease": True,
            "push_branch": True,
            "open_pr": True,
            "merge": False,
            "host_shell": False,
        },
        "surface": "dashboard",
    },
)

_CACHE_LOCK = threading.Lock()
_CACHE_DIR_SIG: tuple[Any, ...] | None = None
_CACHE_ROLES: dict[str, RoleSpec] = {}
_CACHE_FILE_ENTRIES: dict[str, tuple[int, RoleSpec]] = {}
_CACHE_ERRORS: dict[str, list[str]] = {}


def clear_roles_cache() -> None:
    """Clear in-memory roles and validation cache (useful in tests)."""
    global _CACHE_DIR_SIG, _CACHE_ROLES, _CACHE_FILE_ENTRIES, _CACHE_ERRORS
    with _CACHE_LOCK:
        _CACHE_DIR_SIG = None
        _CACHE_ROLES = {}
        _CACHE_FILE_ENTRIES = {}
        _CACHE_ERRORS = {}


def load_roles(directory: Path | None = None) -> dict[str, RoleSpec]:
    """Load every role in ``directory`` with an mtime-keyed cache.

    Schema errors and broken files are surfaced in the returned dictionary
    as invalid roles (dispatchable=False, valid=False, errors=[...]) instead
    of being silently dropped.
    """
    directory = directory if directory is not None else roles_dir()
    if directory is None:
        roles: dict[str, RoleSpec] = {}
        for raw in _FALLBACK_ROLES:
            roles.setdefault(raw["name"], parse_role(raw, "<builtin>"))
        return roles

    files = sorted(directory.glob("*.yml")) + sorted(directory.glob("*.yaml"))
    file_stats: list[tuple[str, int]] = []
    for p in files:
        try:
            file_stats.append((p.name, p.stat().st_mtime_ns))
        except OSError:
            file_stats.append((p.name, -1))
    dir_sig = (str(directory.resolve()), tuple(file_stats))

    global _CACHE_DIR_SIG, _CACHE_ROLES, _CACHE_ERRORS
    with _CACHE_LOCK:
        if _CACHE_DIR_SIG == dir_sig:
            return dict(_CACHE_ROLES)

        new_roles: dict[str, RoleSpec] = {}
        new_errors: dict[str, list[str]] = {}

        for path, (_, mtime) in zip(files, file_stats, strict=True):
            cached = _CACHE_FILE_ENTRIES.get(str(path))
            if cached is not None and cached[0] == mtime:
                spec = cached[1]
            else:
                spec = read_and_parse_role_file(path)
                _CACHE_FILE_ENTRIES[str(path)] = (mtime, spec)
            new_roles[spec.name] = spec
            if not spec.valid and spec.errors:
                new_errors[path.name] = list(spec.errors)

        for raw in _FALLBACK_ROLES:
            new_roles.setdefault(raw["name"], parse_role(raw, "<builtin>"))

        _CACHE_DIR_SIG = dir_sig
        _CACHE_ROLES = new_roles
        _CACHE_ERRORS = new_errors
        return dict(_CACHE_ROLES)


def role_validation_errors(directory: Path | None = None) -> dict[str, list[str]]:
    """Return map of file name -> list of schema validation errors."""
    load_roles(directory)
    with _CACHE_LOCK:
        return dict(_CACHE_ERRORS)
