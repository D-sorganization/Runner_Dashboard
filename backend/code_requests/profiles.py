"""Agent profile data model, defaults, and durable store (CR-3, Issue #1283).

An Agent Profile is a saved, named, reusable configuration for running Code Requests
through any registered agent provider (planner, executor, or both).
"""

from __future__ import annotations

import builtins
import datetime as _dt_mod
import json
import logging
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import config_schema
from agent_remediation.provider_registry import by_dashboard_id
from code_requests.model import STANDARDS_INJECTION
from pydantic import BaseModel, Field

UTC = getattr(_dt_mod, "UTC", _dt_mod.timezone.utc)  # noqa: UP017
datetime = _dt_mod.datetime

log = logging.getLogger("dashboard.code_requests.profiles")

_DEFAULT_PROFILES_PATH = (
    Path(os.environ["AGENT_PROFILES_PATH"])
    if os.environ.get("AGENT_PROFILES_PATH")
    else Path.home() / "actions-runners" / "dashboard" / "agent_profiles.json"
)


class AgentProfile(BaseModel):
    """Saved, named configuration for an agent provider and run parameters."""

    id: str
    name: str
    description: str = ""
    provider: str
    model: str = ""
    effort: str | None = None
    role: Literal["planner", "executor", "both"] = "both"
    staff_role: str = "chief-architect"
    standards: list[str] = Field(default_factory=list)
    prompt_template: str = ""
    prompt_notes: str = ""
    budget: dict[str, Any] = Field(default_factory=lambda: {"max_cost": 1.0, "max_tokens": 100_000, "max_time_s": 3600})
    runner_target: str = "auto"
    approval_gates: dict[str, bool] = Field(
        default_factory=lambda: {"plan_requires_approval": True, "pr_requires_approval": True}
    )
    branch_policy: str = "feat/cr-"
    is_default: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


def get_default_profiles() -> list[AgentProfile]:
    """Return the canonical seeded default agent profiles."""
    now = datetime.now(UTC).isoformat()
    return [
        AgentProfile(
            id="planner-strong",
            name="Planner Strong (Claude Opus 4)",
            description="Frontier planning agent for decomposing code requests into epics and turnover docs",
            provider="claude_code_cli",
            model="claude-opus-4",
            effort="high",
            role="planner",
            staff_role="chief-architect",
            standards=["tdd", "dbc", "dry", "lod", "security", "docs"],
            prompt_template="",
            prompt_notes="",
            budget={"max_cost": 2.0, "max_tokens": 200_000, "max_time_s": 7200},
            runner_target="auto",
            approval_gates={"plan_requires_approval": True, "pr_requires_approval": True},
            branch_policy="feat/cr-",
            is_default=True,
            created_at=now,
            updated_at=now,
        ),
        AgentProfile(
            id="executor-cli",
            name="Executor CLI (Codex)",
            description="CLI execution agent for implementing targeted issues with test verification",
            provider="codex_cli",
            model="gpt-5-codex",
            effort="medium",
            role="executor",
            staff_role="chief-architect",
            standards=["tdd", "dbc"],
            prompt_template="",
            prompt_notes="",
            budget={"max_cost": 0.5, "max_tokens": 50_000, "max_time_s": 3600},
            runner_target="auto",
            approval_gates={"plan_requires_approval": False, "pr_requires_approval": True},
            branch_policy="feat/cr-",
            is_default=False,
            created_at=now,
            updated_at=now,
        ),
    ]


class AgentProfileStore:
    """JSON-backed persistent store for Agent Profiles."""

    def __init__(self, profiles_path: Path | None = None) -> None:
        self.path = profiles_path or _DEFAULT_PROFILES_PATH

    def _ensure_dir(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[AgentProfile]:
        """List all profiles, seeding default profiles if file does not exist."""
        if not self.path.exists():
            defaults = get_default_profiles()
            self._save_raw([p.model_dump(mode="json") for p in defaults])
            return defaults

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return get_default_profiles()
            return [AgentProfile.model_validate(item) for item in raw]
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            log.warning("Failed to read agent profiles from %s: %s", self.path, exc)
            return get_default_profiles()

    def get(self, profile_id: str) -> AgentProfile | None:
        """Get an agent profile by id."""
        for p in self.list():
            if p.id == profile_id:
                return p
        return None

    def get_default_or_fallback(self, role: str = "both") -> AgentProfile:
        """Get the default profile for a role, or first available matching profile."""
        profiles = self.list()
        for p in profiles:
            if p.is_default and (p.role == role or p.role == "both" or role == "both"):
                return p
        for p in profiles:
            if p.role == role or p.role == "both" or role == "both":
                return p
        return profiles[0] if profiles else get_default_profiles()[0]

    def create(self, profile: AgentProfile) -> AgentProfile:
        """Create a new agent profile, validating provider and unique id."""
        self._validate_profile(profile)
        profiles = self.list()
        if any(p.id == profile.id for p in profiles):
            raise ValueError(f"Agent profile with id '{profile.id}' already exists")

        if profile.is_default:
            for p in profiles:
                if p.role == profile.role:
                    p.is_default = False

        profiles.append(profile)
        self._save_raw([p.model_dump(mode="json") for p in profiles])
        return profile

    def update(self, profile_id: str, updates: dict[str, Any]) -> AgentProfile:
        """Update an existing agent profile."""
        profiles = self.list()
        idx = next((i for i, p in enumerate(profiles) if p.id == profile_id), None)
        if idx is None:
            raise KeyError(f"Agent profile '{profile_id}' not found")

        current = profiles[idx].model_dump(mode="json")
        current.update(updates)
        current["id"] = profile_id  # ID cannot be mutated
        current["updated_at"] = datetime.now(UTC).isoformat()
        updated_profile = AgentProfile.model_validate(current)
        self._validate_profile(updated_profile)

        if updated_profile.is_default:
            for p in profiles:
                if p.id != profile_id and p.role == updated_profile.role:
                    p.is_default = False

        profiles[idx] = updated_profile
        self._save_raw([p.model_dump(mode="json") for p in profiles])
        return updated_profile

    def delete(self, profile_id: str) -> bool:
        """Delete an agent profile by id. Fails if only 1 profile remains."""
        profiles = self.list()
        remaining = [p for p in profiles if p.id != profile_id]
        if len(remaining) == len(profiles):
            raise KeyError(f"Agent profile '{profile_id}' not found")
        if not remaining:
            raise ValueError("Cannot delete the last remaining agent profile")

        # If deleted profile was default, mark the first remaining as default
        if not any(p.is_default for p in remaining):
            remaining[0].is_default = True

        self._save_raw([p.model_dump(mode="json") for p in remaining])
        return True

    def _validate_profile(self, profile: AgentProfile) -> None:
        """Validate profile provider and standards against canonical tables."""
        registry = by_dashboard_id()
        if profile.provider not in registry:
            raise ValueError(f"Unknown provider '{profile.provider}'. Must be one of: {list(registry.keys())}")
        for std in profile.standards:
            if std not in STANDARDS_INJECTION:
                raise ValueError(f"Unknown standard '{std}'. Must be one of: {list(STANDARDS_INJECTION.keys())}")

    def _save_raw(self, items: builtins.list[dict[str, Any]]) -> None:
        self._ensure_dir()
        config_schema.atomic_write_json(self.path, items)


def validate_profile_staff_roles(
    profiles: Sequence[AgentProfile] | AgentProfile | None = None,
    raise_on_error: bool = False,
) -> list[str]:
    """Validate agent profile staff roles against the loaded staff roster.

    Pre: Optional ``profiles`` (single profile, sequence of profiles, or None
         to check all profiles from ``AgentProfileStore``).
    Post: Returns a list of error strings; logs a warning for each error.
          If ``raise_on_error`` is True, raises ValueError when any error is detected.
          Returns empty list when no staff roles directory is located (never crashes startup).
    """
    from staff.roles import load_roles, roles_dir

    r_dir = roles_dir()
    if r_dir is None:
        log.debug("No staff roles directory located; skipping agent profile staff roles validation")
        return []

    try:
        roster = load_roles(r_dir)
    except Exception as exc:
        msg = f"Failed to load staff roles for validation: {exc}"
        log.warning(msg)
        if raise_on_error:
            raise ValueError(msg) from exc
        return [msg]

    if profiles is None:
        try:
            profiles_to_check = AgentProfileStore().list()
        except Exception as exc:
            log.warning("Failed to list agent profiles for staff role validation: %s", exc)
            return []
    elif isinstance(profiles, AgentProfile):
        profiles_to_check = [profiles]
    else:
        profiles_to_check = list(profiles)

    errors: list[str] = []
    for profile in profiles_to_check:
        role_name = profile.staff_role
        spec = roster.get(role_name)
        if not spec:
            errors.append(f"Profile '{profile.id}' has staff_role '{role_name}' which does not exist in loaded roster")
        elif not spec.dispatchable:
            errors.append(
                f"Profile '{profile.id}' has staff_role '{role_name}' which is not dispatchable "
                f"(retired={spec.retired}, surface={spec.surface})"
            )

    if errors:
        for err in errors:
            log.warning("Agent profile staff role validation warning: %s", err)
        if raise_on_error:
            raise ValueError("; ".join(errors))
    return errors


# Validate at import time without crashing the server
try:
    validate_profile_staff_roles(raise_on_error=False)
except Exception:  # noqa: BLE001
    log.exception("Unexpected error during agent profile staff roles initial validation")
