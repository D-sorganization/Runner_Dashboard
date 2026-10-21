"""Declarative per-role provider fallback on capacity / quota errors (#2021).

Roles may declare ``retry.provider_fallbacks`` (or top-level ``provider_fallbacks``)
with explicit ``when`` failure classes, an optional ``from`` provider filter, and a
``to`` block (provider + optional model). Matching runs schedule a new attempt on
the target provider without treating real task failures as fallback triggers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from staff.adapters import READ_ONLY_RUN_PROVIDERS

log = logging.getLogger("dashboard.staff.provider_fallback")

# Failure classes that skip same-provider retry and switch immediately.
IMMEDIATE_FALLBACK_CLASSES = frozenset({"provider_capacity"})

# Only these classes may appear in declarative ``when`` lists (DbC).
ALLOWED_WHEN_CLASSES = frozenset({"provider_capacity", "rate_limited"})

KNOWN_PROVIDERS = frozenset(
    {
        "claude",
        "codex",
        "antigravity",
        "gemini",
        "cursor-agent",
        "ollama",
        "claude-ollama",
        "maxwell",
        "grok-chat",
    }
)


@dataclass(frozen=True)
class ProviderFallbackTarget:
    provider: str
    model: str | None = None


@dataclass(frozen=True)
class ProviderFallbackRule:
    when: tuple[str, ...]
    from_provider: str | None
    target: ProviderFallbackTarget


def _as_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return ()


def parse_provider_fallback_rules(data: dict[str, Any]) -> tuple[ProviderFallbackRule, ...]:
    """Parse ``provider_fallbacks`` from role YAML (under ``retry`` or top-level)."""
    retry_cfg = data.get("retry")
    raw = None
    if isinstance(retry_cfg, dict):
        raw = retry_cfg.get("provider_fallbacks")
    if raw is None:
        raw = data.get("provider_fallbacks")
    if not raw:
        return ()
    if not isinstance(raw, list):
        return ()
    rules: list[ProviderFallbackRule] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        when = _as_tuple(entry.get("when"))
        if not when:
            continue
        from_prov = entry.get("from")
        from_provider = str(from_prov).strip() if from_prov not in (None, "") else None
        to_block = entry.get("to")
        if not isinstance(to_block, dict):
            continue
        to_provider = str(to_block.get("provider") or "").strip()
        if not to_provider:
            continue
        model_raw = to_block.get("model")
        to_model = str(model_raw).strip() if model_raw not in (None, "", "default") else None
        rules.append(
            ProviderFallbackRule(
                when=when,
                from_provider=from_provider,
                target=ProviderFallbackTarget(provider=to_provider, model=to_model),
            )
        )
    return tuple(rules)


def validate_provider_fallback_rules(
    rules: tuple[ProviderFallbackRule, ...],
    role_providers: tuple[str, ...],
) -> list[str]:
    """Return schema validation problems for parsed fallback rules."""
    problems: list[str] = []
    for idx, rule in enumerate(rules):
        prefix = f"provider_fallbacks[{idx}]"
        for cls in rule.when:
            if cls not in ALLOWED_WHEN_CLASSES:
                problems.append(f"{prefix}.when contains unsupported class {cls!r}")
        if rule.from_provider and rule.from_provider not in role_providers:
            problems.append(f"{prefix}.from {rule.from_provider!r} is not in role providers")
        if rule.target.provider not in KNOWN_PROVIDERS:
            problems.append(f"{prefix}.to.provider {rule.target.provider!r} is unknown")
        elif rule.target.provider not in role_providers:
            problems.append(f"{prefix}.to.provider {rule.target.provider!r} is not in role providers")
    return problems


def match_provider_fallback(
    rules: tuple[ProviderFallbackRule, ...],
    failure_class: str,
    current_provider: str,
    *,
    retries_exhausted: bool,
) -> ProviderFallbackRule | None:
    """Return the first matching declarative rule, or None.

    Pre: ``failure_class`` is the classified outcome of the attempt.
    Post: never returns a rule unless ``failure_class`` is listed on the rule's ``when``.
    """
    if failure_class not in ALLOWED_WHEN_CLASSES:
        return None
    immediate = failure_class in IMMEDIATE_FALLBACK_CLASSES
    if not immediate and not retries_exhausted:
        return None
    for rule in rules:
        if failure_class not in rule.when:
            continue
        if rule.from_provider and rule.from_provider != current_provider:
            continue
        return rule
    return None


def fallback_target_usable(
    target_provider: str,
    *,
    adapters: Any,
    role_code_read_only: bool,
    usable: Any,
) -> bool:
    """Whether the fallback target can take an unattended run."""
    if role_code_read_only and target_provider not in READ_ONLY_RUN_PROVIDERS:
        return False
    return bool(usable(target_provider))


def validate_provider_fallback_rules_data(data: dict[str, Any]) -> list[str]:
    """Validate provider_fallbacks embedded in raw role YAML."""
    providers = _as_tuple(data.get("providers"))
    rules = parse_provider_fallback_rules(data)
    return validate_provider_fallback_rules(rules, providers)


def try_declarative_fallback(
    runner: Any,
    latest: Any,
    plan: Any,
    role: Any,
    guard: Any,
    root_id: str,
    *,
    retries_exhausted: bool,
    attempt_run_id: Any,
    claim: Any,
    schedule: Any,
    provenance: Any,
    can_run_unattended: Any,
    read_only_kwargs: Any,
) -> bool:
    """Schedule a declarative fallback attempt when rules match (#2021)."""
    from staff.plan import RunPlan  # noqa: PLC0415
    from staff.store import RunRecord  # noqa: PLC0415

    rules = getattr(role, "provider_fallbacks", ())
    if not rules:
        return False
    rule = match_provider_fallback(
        rules,
        latest.failure_class,
        plan.provider,
        retries_exhausted=retries_exhausted,
    )
    if rule is None:
        return False
    target = rule.target.provider
    if not can_run_unattended(runner._adapters, target) or (
        role.code_read_only and target not in READ_ONLY_RUN_PROVIDERS
    ):
        log.info(
            "declarative fallback to %s skipped: provider unavailable or chat-only",
            target,
        )
        return False
    can_run, _ = guard.can_run(role)
    if not can_run:
        return False

    next_attempt = latest.attempt + 1
    attempt_id = attempt_run_id(root_id, next_attempt)
    next_branch = f"{plan.branch}-fallback-{next_attempt}"
    fallback_model = rule.target.model or plan.model
    argv = runner._adapters[target].build_command(plan.prompt, "<workdir>", fallback_model, **read_only_kwargs(role))
    fallback_plan = RunPlan(
        role=plan.role,
        provider=target,
        model=fallback_model,
        repo=plan.repo,
        target_kind=plan.target_kind,
        target_ref=plan.target_ref,
        operator_prompt=plan.operator_prompt,
        prompt=plan.prompt,
        argv=argv,
        branch=next_branch,
        lease_ritual=plan.lease_ritual,
        consolidation=plan.consolidation,
        focus=plan.focus,
        head_ref=plan.head_ref,
    )
    fallback_rec = RunRecord(
        id=attempt_id,
        role=plan.role,
        provider=target,
        model=fallback_model,
        machine=runner.machine,
        repo=plan.repo,
        target_kind=plan.target_kind,
        target_ref=plan.target_ref,
        prompt=plan.prompt,
        requested_by=latest.requested_by,
        on_behalf_of=latest.on_behalf_of,
        branch=next_branch,
        strategy_mode=plan.strategy_mode,
        retry_of=root_id,
        attempt=next_attempt,
        max_attempts=latest.max_attempts + 1,
        fallback_provider=target,
        **provenance(latest),
    )
    if not claim(runner.store, fallback_rec):
        return False
    runner.store.append_event(
        root_id,
        "fallback",
        (
            f"{latest.failure_class} on {plan.provider}; declarative fallback to "
            f"{target}" + (f" ({fallback_model})" if fallback_model else "")
        ),
    )
    schedule(runner, fallback_rec, fallback_plan, 0.0)
    return True
