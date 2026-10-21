"""Resolve provider + model + tier for failover launches (#2047)."""

from __future__ import annotations

from staff.adapters import ProviderAdapter
from staff.model_resolution import resolve_staff_run_model_detail
from staff.model_tiers import TierName, normalize_tier
from staff.plan import RunRequest
from staff.provider_failover_config import FailoverStep
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
    """Model for one chain step at ``active_tier`` (never below the role's tier)."""
    base = role_base_tier(role)
    tier = active_tier
    order = ("routine", "standard", "complex")
    if order.index(tier) < order.index(base):
        tier = base
    requested = req.model if req is not None else None
    step_model = step.model if step is not None else None
    resolved = resolve_staff_run_model_detail(
        requested=requested,
        role_model=step_model or role.model,
        provider_id=provider,
        adapter=adapter,
        role_tier=tier,
    )
    return resolved.model, resolved.tier


def slot_key(provider: str, tier: str) -> str:
    return f"{provider}:{tier}"
