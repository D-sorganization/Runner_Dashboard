"""Staff role registry — reads ``staff/roles/*.yml`` published by Repository_Management.

The dashboard never imports Repository_Management code. It locates the role
directory on disk (operator override → sibling checkout → fleet-sync copy)
and parses the YAML with the same shape that
``Repository_Management/shared_scripts/staff_roles.py`` validates.

A small built-in fallback roster keeps the API usable on a node that has no
RM checkout (e.g. a fresh laptop), so the Staff tab renders instead of 503ing.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

log = logging.getLogger("dashboard.staff.roles")

ROLES_DIR_ENV = "STAFF_ROLES_DIR"
SCHEMA_PATH_ENV = "STAFF_SCHEMA_PATH"
_SIBLING_RELATIVE = ("Repository_Management", "staff", "roles")
_SIBLING_SCHEMA_RELATIVE = ("Repository_Management", "staff", "schema.json")
_LOCAL_SCHEMA_PATH = Path(__file__).parent / "schema.json"


@dataclass(frozen=True)
class RoleSpec:
    """Flat, typed view of one role file (LoD: handlers only see this)."""

    name: str
    title: str
    summary: str = ""
    playbook: str = ""
    prompt_template: str | None = None
    instructions: str = ""
    providers: tuple[str, ...] = ("claude",)
    model: str | None = None
    schedule: str | None = None
    window: dict[str, str] | None = None
    repos: tuple[str, ...] = ()
    scope: dict[str, Any] = field(default_factory=dict)
    budget_usd_per_run: float = 0.0
    budget_usd_per_day: float = 0.0
    budget_max_minutes: float = 240.0
    idle_minutes: float = 20.0
    permissions: dict[str, bool] = field(default_factory=dict)
    reports_to: str = ""
    holds: tuple[str, ...] = ()
    surface: str = "dashboard"
    retired: bool = False
    retired_reason: str = ""
    # Optional ``strategy:`` block (RM#1690 / #1213), e.g.
    # ``{"consolidate_when": {"open_prs": 6, "utilisation_pct": 70}}``. Kept as-is; unknown keys ignored downstream.
    strategy: dict[str, Any] = field(default_factory=dict)
    persona: str = ""
    chat: dict[str, Any] = field(default_factory=dict)
    group: str | None = None
    valid: bool = True
    errors: tuple[str, ...] = ()
    source_path: str = ""

    @property
    def dispatchable(self) -> bool:
        """True when the dashboard can run this role as a CLI subprocess."""
        return self.valid and not self.retired and self.surface in {"dashboard", "both"} and bool(self.providers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "summary": self.summary,
            "playbook": self.playbook,
            "prompt_template": self.prompt_template,
            "instructions": self.instructions,
            "providers": list(self.providers),
            "model": self.model,
            "schedule": self.schedule,
            "window": self.window,
            "repos": list(self.repos),
            "scope": dict(self.scope),
            "budget": {
                "usd_per_run": self.budget_usd_per_run,
                "usd_per_day": self.budget_usd_per_day,
                "max_minutes": self.budget_max_minutes,
                "idle_minutes": self.idle_minutes,
            },
            "idle_minutes": self.idle_minutes,
            "permissions": dict(self.permissions),
            "reports_to": self.reports_to,
            "holds": list(self.holds),
            "surface": self.surface,
            "retired": self.retired,
            "retired_reason": self.retired_reason,
            "strategy": dict(self.strategy),
            "persona": self.persona,
            "chat": dict(self.chat),
            "group": self.group,
            "valid": self.valid,
            "errors": list(self.errors),
            "error": self.errors[0] if self.errors else None,
            "dispatchable": self.dispatchable,
            "source_path": self.source_path,
        }


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


_VALIDATOR: jsonschema.Draft202012Validator | None = None
_VALIDATOR_LOCK = threading.Lock()


def _get_validator() -> jsonschema.Draft202012Validator:
    global _VALIDATOR
    with _VALIDATOR_LOCK:
        if _VALIDATOR is not None:
            return _VALIDATOR
        path = schema_path()
        try:
            schema_data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            schema_data = json.loads(_LOCAL_SCHEMA_PATH.read_text(encoding="utf-8"))

        for clause in schema_data.get("allOf", []):
            if "if" in clause and "properties" in clause["if"] and "retired" in clause["if"]["properties"]:
                reqs = clause["if"].setdefault("required", [])
                if "retired" not in reqs:
                    reqs.append("retired")

        providers_prop = schema_data.get("properties", {}).get("providers", {})
        if "items" in providers_prop and "enum" in providers_prop["items"]:
            del providers_prop["items"]["enum"]

        props = schema_data.setdefault("properties", {})
        props.setdefault("persona", {"type": "string"})
        props.setdefault("group", {"type": ["string", "null"]})
        props.setdefault(
            "chat",
            {
                "type": "object",
                "properties": {
                    "providers": {"type": "array", "items": {"type": "string"}},
                    "read_only_tools": {"type": "array", "items": {"type": "string"}},
                },
            },
        )
        _VALIDATOR = jsonschema.Draft202012Validator(schema_data)
        return _VALIDATOR


def validate_role_data(data: Any) -> list[str]:
    """Validate parsed role mapping against staff/schema.json."""
    if not isinstance(data, dict):
        return ["top level is not a mapping"]
    validator = _get_validator()
    errors: list[str] = []
    for err in sorted(validator.iter_errors(data), key=lambda e: (list(e.path), e.message)):
        loc = ".".join(str(p) for p in err.path)
        if loc:
            errors.append(f"{loc}: {err.message}")
        else:
            errors.append(err.message)
    return errors


def _as_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list | tuple):
        return tuple(str(v) for v in value)
    return ()


def parse_role(
    data: dict[str, Any],
    source_path: str = "",
    *,
    errors: tuple[str, ...] = (),
) -> RoleSpec:
    """Build a RoleSpec from a parsed YAML mapping.

    Pre: ``data`` is a dict. Unknown keys are ignored so the RM schema can grow
    without breaking older dashboards (additive contract).
    """
    name = str(data.get("name") or (Path(source_path).stem if source_path else "")).strip()
    if not name:
        name = "unnamed"
    budget = data.get("budget") or {}
    perms = data.get("permissions") or {}
    window = data.get("window") or None
    strategy = data.get("strategy")
    max_minutes = budget.get("max_minutes")
    if max_minutes is None:
        max_minutes = data.get("max_minutes", 240.0)
    idle_minutes = budget.get("idle_minutes")
    if idle_minutes is None:
        idle_minutes = data.get("idle_minutes", 20.0)

    try:
        usd_run = float(budget.get("usd_per_run") or 0.0)
    except (TypeError, ValueError):
        usd_run = 0.0
    try:
        usd_day = float(budget.get("usd_per_day") or 0.0)
    except (TypeError, ValueError):
        usd_day = 0.0
    try:
        max_m = float(max_minutes)
    except (TypeError, ValueError):
        max_m = 240.0
    try:
        idle_m = float(idle_minutes)
    except (TypeError, ValueError):
        idle_m = 20.0

    prompt_template = str(data["prompt_template"]) if data.get("prompt_template") not in (None, "") else None
    scope = dict(data["scope"]) if isinstance(data.get("scope"), dict) else {}
    persona = str(data.get("persona") or "")
    group = str(data["group"]) if data.get("group") is not None else None
    chat = dict(data["chat"]) if isinstance(data.get("chat"), dict) else {}
    retired_reason = str(data.get("retired_reason") or "")

    return RoleSpec(
        name=name,
        title=str(data.get("title") or name),
        summary=str(data.get("summary") or ""),
        playbook=str(data.get("playbook") or ""),
        prompt_template=prompt_template,
        instructions=str(data.get("instructions") or ""),
        providers=_as_tuple(data.get("providers")) or ("claude",),
        model=(str(data["model"]) if data.get("model") not in (None, "", "default") else None),
        schedule=(str(data["schedule"]) if data.get("schedule") else None),
        window=(
            {"start": str(window.get("start", "")), "end": str(window.get("end", ""))}
            if isinstance(window, dict)
            else None
        ),
        repos=_as_tuple(data.get("repos")),
        scope=scope,
        budget_usd_per_run=usd_run,
        budget_usd_per_day=usd_day,
        budget_max_minutes=max_m,
        idle_minutes=idle_m,
        permissions=({str(k): bool(v) for k, v in perms.items()} if isinstance(perms, dict) else {}),
        reports_to=str(data.get("reports_to") or ""),
        holds=_as_tuple(data.get("holds")),
        surface=str(data.get("surface") or "dashboard"),
        retired=bool(data.get("retired", False)),
        retired_reason=retired_reason,
        strategy=dict(strategy) if isinstance(strategy, dict) else {},
        persona=persona,
        chat=chat,
        group=group,
        valid=len(errors) == 0,
        errors=errors,
        source_path=source_path,
    )


_FALLBACK_ROLES: tuple[dict[str, Any], ...] = (
    {
        "name": "ad-hoc",
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
    global _CACHE_DIR_SIG, _CACHE_ROLES, _CACHE_FILE_ENTRIES, _CACHE_ERRORS, _VALIDATOR
    with _CACHE_LOCK:
        _CACHE_DIR_SIG = None
        _CACHE_ROLES = {}
        _CACHE_FILE_ENTRIES = {}
        _CACHE_ERRORS = {}
    with _VALIDATOR_LOCK:
        _VALIDATOR = None


def _read_and_parse_role_file(path: Path) -> RoleSpec:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return RoleSpec(
            name=path.stem,
            title=path.stem,
            valid=False,
            errors=(f"cannot read file: {exc}",),
            source_path=str(path),
        )
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        return RoleSpec(
            name=path.stem,
            title=path.stem,
            valid=False,
            errors=(f"YAML syntax error: {exc}",),
            source_path=str(path),
        )
    if not isinstance(data, dict):
        return RoleSpec(
            name=path.stem,
            title=path.stem,
            valid=False,
            errors=("top level is not a mapping",),
            source_path=str(path),
        )
    errs = validate_role_data(data)
    return parse_role(data, str(path), errors=tuple(errs))


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
                spec = _read_and_parse_role_file(path)
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
