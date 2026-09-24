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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("dashboard.staff.roles")

ROLES_DIR_ENV = "STAFF_ROLES_DIR"
_SIBLING_RELATIVE = ("Repository_Management", "staff", "roles")


@dataclass(frozen=True)
class RoleSpec:
    """Flat, typed view of one role file (LoD: handlers only see this)."""

    name: str
    title: str
    summary: str = ""
    playbook: str = ""
    instructions: str = ""
    providers: tuple[str, ...] = ("claude",)
    model: str | None = None
    schedule: str | None = None
    window: dict[str, str] | None = None
    repos: tuple[str, ...] = ()
    budget_usd_per_run: float = 0.0
    budget_usd_per_day: float = 0.0
    budget_max_minutes: float = 240.0
    idle_minutes: float = 20.0
    permissions: dict[str, bool] = field(default_factory=dict)
    reports_to: str = ""
    holds: tuple[str, ...] = ()
    surface: str = "dashboard"
    retired: bool = False
    # Optional ``strategy:`` block (RM#1690 / #1213), e.g.
    # ``{"consolidate_when": {"open_prs": 6, "utilisation_pct": 70}}``. Kept as-is; unknown keys ignored downstream.
    strategy: dict[str, Any] = field(default_factory=dict)
    source_path: str = ""

    @property
    def dispatchable(self) -> bool:
        """True when the dashboard can run this role as a CLI subprocess."""
        return not self.retired and self.surface in {"dashboard", "both"} and bool(self.providers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "summary": self.summary,
            "playbook": self.playbook,
            "providers": list(self.providers),
            "model": self.model,
            "schedule": self.schedule,
            "window": self.window,
            "repos": list(self.repos),
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
            "strategy": dict(self.strategy),
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


def _as_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list | tuple):
        return tuple(str(v) for v in value)
    return ()


def parse_role(data: dict[str, Any], source_path: str = "") -> RoleSpec:
    """Build a RoleSpec from a parsed YAML mapping.

    Pre: ``data['name']`` is a non-empty string. Unknown keys are ignored so the
    RM schema can grow without breaking older dashboards (additive contract).
    """
    name = str(data.get("name", "")).strip()
    assert name, f"role file {source_path or '<inline>'} has no name"  # noqa: S101
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
    return RoleSpec(
        name=name,
        title=str(data.get("title") or name),
        summary=str(data.get("summary") or ""),
        playbook=str(data.get("playbook") or ""),
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
        budget_usd_per_run=float(budget.get("usd_per_run") or 0.0),
        budget_usd_per_day=float(budget.get("usd_per_day") or 0.0),
        budget_max_minutes=float(max_minutes),
        idle_minutes=float(idle_minutes),
        permissions=({str(k): bool(v) for k, v in perms.items()} if isinstance(perms, dict) else {}),
        reports_to=str(data.get("reports_to") or ""),
        holds=_as_tuple(data.get("holds")),
        surface=str(data.get("surface") or "dashboard"),
        retired=bool(data.get("retired", False)),
        strategy=dict(strategy) if isinstance(strategy, dict) else {},
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


def load_roles(directory: Path | None = None) -> dict[str, RoleSpec]:
    """Load every ``*.yml`` in the role directory; invalid files are logged and skipped.

    Post: the result always contains the built-in ``ad-hoc`` role.
    """
    roles: dict[str, RoleSpec] = {}
    directory = directory if directory is not None else roles_dir()
    if directory is not None:
        for path in sorted(directory.glob("*.yml")) + sorted(directory.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if not isinstance(data, dict):
                    raise ValueError("top level is not a mapping")
                spec = parse_role(data, str(path))
                roles[spec.name] = spec
            except Exception as exc:  # noqa: BLE001
                log.warning("staff: skipping role file %s: %s", path, exc)
    for raw in _FALLBACK_ROLES:
        roles.setdefault(raw["name"], parse_role(raw, "<builtin>"))
    return roles
