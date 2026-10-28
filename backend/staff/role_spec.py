"""RoleSpec parsing and serialization (split from ``staff.roles`` for the 500-line cap)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from staff.model_tiers import validate_role_tier
from staff.provider_failover_config import (
    FailoverStep,
    parse_role_failover_chain,
    validate_failover_chain,
)
from staff.provider_fallback import (
    ProviderFallbackRule,
    parse_provider_fallback_rules,
    validate_provider_fallback_rules,
)
from staff.validator import validate_role_data


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
    tier: str = "routine"
    allow_tier_escalation: bool = False
    schedule: str | None = None
    window: dict[str, str] | None = None
    repos: tuple[str, ...] = ()
    scope: dict[str, Any] = field(default_factory=dict)
    budget_usd_per_run: float = 0.0
    budget_usd_per_day: float = 0.0
    budget_max_minutes: float = 240.0
    budget_max_window_percent: float | None = None
    idle_minutes: float = 20.0
    permissions: dict[str, Any] = field(default_factory=dict)
    reports_to: str = ""
    holds: tuple[str, ...] = ()
    surface: str = "dashboard"
    retired: bool = False
    retired_reason: str = ""
    strategy: dict[str, Any] = field(default_factory=dict)
    persona: dict[str, Any] | str = ""
    avatar: str | None = None
    chat: dict[str, Any] = field(default_factory=dict)
    tools: tuple[str, ...] = ()
    group: str | None = None
    max_attempts: int = 2
    fallback_providers: tuple[str, ...] = ()
    provider_fallbacks: tuple[ProviderFallbackRule, ...] = ()
    failover_chain: tuple[FailoverStep, ...] = ()
    valid: bool = True
    errors: tuple[str, ...] = ()
    source_path: str = ""

    @property
    def opens_pr(self) -> bool:
        """Runs of this role promise a pull request (``permissions.open_pr``; #1516)."""
        return bool(self.permissions.get("open_pr"))

    @property
    def code_read_only(self) -> bool:
        """Unattended runs of this role are code-read-only (explicit ``permissions.code_read_only``; #1659)."""
        return self.permissions.get("code_read_only") is True

    @property
    def fleet_actions(self) -> tuple[str, ...]:
        """Allowed fleet maintenance actions (RM#1734, RD#1310)."""
        actions = self.permissions.get("fleet_actions")
        if isinstance(actions, (list, tuple)):
            return tuple(str(a) for a in actions)
        return ()

    @property
    def approvals(self) -> dict[str, str]:
        """Per-action approval requirements (RM#1734, RD#1310)."""
        appr = self.permissions.get("approvals") or self.permissions.get("action_approvals")
        if isinstance(appr, dict):
            return {str(k): str(v) for k, v in appr.items()}
        return {}

    @property
    def defers_to(self) -> tuple[str, ...]:
        """Roles this persona delegates to (RM#1735, RD#1308)."""
        if isinstance(self.persona, dict):
            targets = self.persona.get("defers_to")
            if isinstance(targets, (list, tuple)):
                return tuple(str(t) for t in targets)
        return ()

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
            "prompt_template": self.prompt_template,
            "instructions": self.instructions,
            "providers": list(self.providers),
            "model": self.model,
            "tier": self.tier,
            "allow_tier_escalation": self.allow_tier_escalation,
            "schedule": self.schedule,
            "window": self.window,
            "repos": list(self.repos),
            "scope": dict(self.scope),
            "budget": {
                "usd_per_run": self.budget_usd_per_run,
                "usd_per_day": self.budget_usd_per_day,
                "max_minutes": self.budget_max_minutes,
                "max_window_percent": self.budget_max_window_percent,
                "idle_minutes": self.idle_minutes,
            },
            "idle_minutes": self.idle_minutes,
            "permissions": dict(self.permissions),
            "fleet_actions": list(self.fleet_actions),
            "approvals": dict(self.approvals),
            "reports_to": self.reports_to,
            "holds": list(self.holds),
            "surface": self.surface,
            "retired": self.retired,
            "retired_reason": self.retired_reason,
            "strategy": dict(self.strategy),
            "persona": self.persona,
            "avatar": self.avatar,
            "chat": dict(self.chat),
            "tools": list(self.tools),
            "group": self.group,
            "max_attempts": self.max_attempts,
            "fallback_providers": list(self.fallback_providers),
            "provider_fallbacks": [
                {
                    "when": list(r.when),
                    "from": r.from_provider,
                    "to": {"provider": r.target.provider, "model": r.target.model},
                }
                for r in self.provider_fallbacks
            ],
            "failover_chain": [{"provider": s.provider, "model": s.model} for s in self.failover_chain],
            "valid": self.valid,
            "errors": list(self.errors),
            "error": self.errors[0] if self.errors else None,
            "dispatchable": self.dispatchable,
            "source_path": self.source_path,
        }


def _as_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list | tuple):
        return tuple(str(v) for v in value)
    return ()


def _parse_permissions(perms: Any) -> dict[str, Any]:
    if not isinstance(perms, dict):
        return {}
    res: dict[str, Any] = {}
    for k, v in perms.items():
        if k == "fleet_actions":
            res[k] = list(_as_tuple(v))
        elif k in ("approvals", "action_approvals") and isinstance(v, dict):
            res[k] = {str(ak): str(av) for ak, av in v.items()}
        elif isinstance(v, bool):
            res[k] = v
        else:
            res[k] = v
    return res


def parse_role(
    data: dict[str, Any],
    source_path: str = "",
    *,
    errors: tuple[str, ...] = (),
) -> RoleSpec:
    """Build a RoleSpec from a parsed YAML mapping."""
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
        window_pct: float | None = float(budget["max_window_percent"])
    except (KeyError, TypeError, ValueError):
        window_pct = None
    if window_pct is not None and not 0 < window_pct <= 100:
        window_pct = None
    try:
        idle_m = float(idle_minutes)
    except (TypeError, ValueError):
        idle_m = 20.0

    prompt_template = str(data["prompt_template"]) if data.get("prompt_template") not in (None, "") else None
    scope = dict(data["scope"]) if isinstance(data.get("scope"), dict) else {}
    raw_persona = data.get("persona")
    if isinstance(raw_persona, dict):
        persona: dict[str, Any] | str = dict(raw_persona)
    else:
        persona = str(raw_persona or "")
    group = str(data["group"]) if data.get("group") is not None else None
    chat = dict(data["chat"]) if isinstance(data.get("chat"), dict) else {}
    tools = _as_tuple(data.get("tools"))
    retired_reason = str(data.get("retired_reason") or "")
    raw_retry = data.get("retry")
    retry_cfg: dict[str, Any] = raw_retry if isinstance(raw_retry, dict) else {}
    try:
        max_att = int(retry_cfg.get("max_attempts") or data.get("max_attempts") or 2)
    except (TypeError, ValueError):
        max_att = 2
    fallback_provs = _as_tuple(retry_cfg.get("fallback_providers") or data.get("fallback_providers"))
    provider_fallback_rules = parse_provider_fallback_rules(data)
    merged_errors = list(errors)
    merged_errors.extend(validate_provider_fallback_rules(provider_fallback_rules, _as_tuple(data.get("providers"))))
    failover_chain = parse_role_failover_chain(data) or ()
    merged_errors.extend(validate_failover_chain(failover_chain, _as_tuple(data.get("providers"))))
    role_tier = str(data.get("tier") or "routine").strip().lower()
    merged_errors.extend(validate_role_tier(role_tier))
    allow_escalation = bool(data.get("allow_tier_escalation", False))

    return RoleSpec(
        name=name,
        title=str(data.get("title") or name),
        summary=str(data.get("summary") or ""),
        playbook=str(data.get("playbook") or ""),
        prompt_template=prompt_template,
        instructions=str(data.get("instructions") or ""),
        providers=_as_tuple(data.get("providers")) or ("claude",),
        model=(str(data["model"]) if data.get("model") not in (None, "", "default") else None),
        tier=role_tier if role_tier in ("routine", "standard", "complex") else "routine",
        allow_tier_escalation=allow_escalation,
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
        budget_max_window_percent=window_pct,
        idle_minutes=idle_m,
        permissions=_parse_permissions(perms),
        reports_to=str(data.get("reports_to") or ""),
        holds=_as_tuple(data.get("holds")),
        surface=str(data.get("surface") or "dashboard"),
        retired=bool(data.get("retired", False)),
        retired_reason=retired_reason,
        strategy=dict(strategy) if isinstance(strategy, dict) else {},
        persona=persona,
        avatar=(str(data["avatar"]) if data.get("avatar") not in (None, "") else None),
        chat=chat,
        tools=tools,
        group=group,
        max_attempts=max_att,
        fallback_providers=fallback_provs,
        provider_fallbacks=provider_fallback_rules,
        failover_chain=failover_chain,
        valid=len(merged_errors) == 0,
        errors=tuple(merged_errors),
        source_path=source_path,
    )


def read_and_parse_role_file(path: Path) -> RoleSpec:
    """Load one role YAML file into a RoleSpec (invalid files become ``valid=False``)."""
    import yaml

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return RoleSpec(
            name=path.stem,
            title=path.stem,
            providers=(),
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
            providers=(),
            valid=False,
            errors=(f"YAML syntax error: {exc}",),
            source_path=str(path),
        )
    if not isinstance(data, dict):
        return RoleSpec(
            name=path.stem,
            title=path.stem,
            providers=(),
            valid=False,
            errors=("top level is not a mapping",),
            source_path=str(path),
        )
    errs = validate_role_data(data)
    return parse_role(data, str(path), errors=tuple(errs))
