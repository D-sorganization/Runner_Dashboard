"""Resolve the effective model and tier for a staff run plan (issues #2053, #2047).

Precedence: request body ``model``, role YAML ``model``, role tier table for the
chosen provider, then repo ``staff_provider_defaults.json`` and the adapter default.
"""

from __future__ import annotations

from dataclasses import dataclass

from staff.adapters import ProviderAdapter
from staff.model_tiers import TierName, allowed_models_for_provider, model_for_provider_tier, normalize_tier
from staff.provider_defaults import default_model_for_provider

_UNSET_ROLE_MODELS = frozenset({"", "default"})


def _normalized(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True)
class ResolvedModel:
    model: str | None
    tier: TierName
    source: str  # request | role_model | role_tier | provider_default


def resolve_staff_run_model(
    *,
    requested: str | None,
    role_model: str | None,
    provider_id: str,
    adapter: ProviderAdapter,
    role_tier: str | None = "routine",
) -> str | None:
    """Return the model id only (backward compatible)."""
    return resolve_staff_run_model_detail(
        requested=requested,
        role_model=role_model,
        provider_id=provider_id,
        adapter=adapter,
        role_tier=role_tier,
    ).model


def resolve_staff_run_model_detail(
    *,
    requested: str | None,
    role_model: str | None,
    provider_id: str,
    adapter: ProviderAdapter,
    role_tier: str | None = "routine",
) -> ResolvedModel:
    """Return model, effective tier, and which precedence level won."""
    assert provider_id.strip(), "provider_id must be non-empty"  # noqa: S101
    tier = normalize_tier(role_tier)
    explicit = _normalized(requested)
    if explicit is not None:
        _assert_allowed(provider_id, explicit, adapter)
        return ResolvedModel(model=explicit, tier=tier, source="request")
    from_role = _normalized(role_model)
    if from_role is not None and from_role.lower() not in _UNSET_ROLE_MODELS:
        _assert_allowed(provider_id, from_role, adapter)
        return ResolvedModel(model=from_role, tier=tier, source="role_model")
    from_tier = model_for_provider_tier(provider_id, tier)
    if from_tier:
        _assert_allowed(provider_id, from_tier, adapter)
        return ResolvedModel(model=from_tier, tier=tier, source="role_tier")
    from_adapter = _normalized(getattr(adapter, "default_model", None))
    if from_adapter is not None:
        return ResolvedModel(model=from_adapter, tier=tier, source="provider_default")
    repo = default_model_for_provider(provider_id)
    if repo:
        _assert_allowed(provider_id, repo, adapter)
        return ResolvedModel(model=repo, tier=tier, source="provider_default")
    return ResolvedModel(model=None, tier=tier, source="provider_default")


def _assert_allowed(provider_id: str, model: str, adapter: ProviderAdapter) -> None:
    allowed = allowed_models_for_provider(provider_id, adapter)
    if model not in allowed:
        raise ValueError(f"model {model!r} is not valid for provider {provider_id!r}")
