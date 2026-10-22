"""Resolve provider + model + tier for failover launches (#2047)."""

from __future__ import annotations

from staff.adapters import ADAPTERS, ProviderAdapter
from staff.model_resolution import resolve_staff_run_model_detail
from staff.model_tiers import TIER_NAMES, TierName, model_for_provider_tier, normalize_tier
from staff.plan import RunRequest
from staff.provider_failover_config import FailoverStep, resolve_failover_chain
from staff.roles import RoleSpec


def role_base_tier(role: RoleSpec) -> TierName:
    return normalize_tier(role.tier)


def resolve_launch_model(
    *,
    req: RunRequest | None,
    role: RoleSpec,
    provider: str,
    adapter: ProviderAdapter,
    step: FailoverStep | None,
    active_tier: TierName,
) -> tuple[str | None, TierName]:
    """Resolve one chain step without carrying a primary-provider model across vendors."""
    base = role_base_tier(role)
    order = ("routine", "standard", "complex")
    requested_tiers = [base, normalize_tier(active_tier)]
    if req is not None and req.model_tier:
        requested_tiers.append(normalize_tier(req.model_tier))
    tier = max(requested_tiers, key=order.index)

    chain = resolve_failover_chain(role, requested_provider=req.provider if req else None)
    primary_provider = req.provider if req is not None and req.provider else chain[0].provider if chain else provider
    requested = req.model if req is not None else None
    if requested and provider != primary_provider:
        primary_adapter = ADAPTERS.get(primary_provider)
        if primary_adapter is None:
            raise ValueError(f"unknown primary provider '{primary_provider}' for explicit model validation")
        # The explicit model belongs to the intended primary; validate it there even when
        # a fallback step is selected, then resolve this step using its own tier policy.
        resolve_staff_run_model_detail(
            requested=requested,
            role_model=None,
            provider_id=primary_provider,
            adapter=primary_adapter,
            role_tier=tier,
        )
        requested = None
    step_model = step.model if step is not None else None
    if step_model:
        configured_tier = next(
            (candidate for candidate in TIER_NAMES if model_for_provider_tier(provider, candidate) == step_model),
            None,
        )
        if configured_tier is not None and order.index(configured_tier) < order.index(tier):
            step_model = model_for_provider_tier(provider, tier)
    role_model = step_model or (role.model if provider == primary_provider else None)
    resolved = resolve_staff_run_model_detail(
        requested=requested,
        role_model=role_model,
        provider_id=provider,
        adapter=adapter,
        role_tier=tier,
    )
    return resolved.model, resolved.tier


def slot_key(provider: str, tier: str) -> str:
    return f"{provider}:{tier}"
