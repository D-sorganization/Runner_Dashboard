"""Resolve provider + model for pre-launch failover (#2047 slice 3b)."""

from __future__ import annotations

from staff.adapters import ProviderAdapter
from staff.model_resolution import resolve_staff_run_model
from staff.plan import RunRequest
from staff.provider_failover_config import FailoverStep
from staff.roles import RoleSpec

TierName = str


def role_base_tier(role: RoleSpec) -> TierName:
    text = (getattr(role, "tier", None) or "routine").strip().lower()
    if text in ("routine", "standard", "complex"):
        return text
    return "routine"


def resolve_launch_model(
    *,
    req: RunRequest | None,
    role: RoleSpec,
    provider: str,
    adapter: ProviderAdapter,
    step: FailoverStep | None,
    active_tier: TierName,
) -> tuple[str | None, TierName]:
    requested = req.model if req is not None else None
    step_model = step.model if step is not None else None
    model = resolve_staff_run_model(
        requested=requested,
        role_model=step_model or role.model,
        provider_id=provider,
        adapter=adapter,
    )
    return model, active_tier or role_base_tier(role)


def slot_key(provider: str, tier: str) -> str:
    return f"{provider}:{tier}"
