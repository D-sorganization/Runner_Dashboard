"""Maxwell must not appear in staff or code-request provider routing (2026-10-22)."""

from __future__ import annotations

import pytest
from agent_remediation.provider_registry import PROVIDER_REGISTRY
from code_requests.executor_router import DEFAULT_PROVIDERS_BY_TIER, ESTIMATED_COST_PER_DISPATCH
from staff import adapters as adapters_mod
from staff.provider_fallback import KNOWN_PROVIDERS
from staff.quota import BILLING


@pytest.mark.unit
def test_maxwell_is_not_selectable_anywhere_in_routing() -> None:
    registry_ids = {e.dashboard_id for e in PROVIDER_REGISTRY}
    tier_providers = {p for providers in DEFAULT_PROVIDERS_BY_TIER.values() for p in providers}

    assert "maxwell" not in adapters_mod.ADAPTERS
    assert "maxwell" not in registry_ids
    assert "maxwell" not in KNOWN_PROVIDERS
    assert "maxwell" not in tier_providers
    assert "maxwell" not in ESTIMATED_COST_PER_DISPATCH
    assert "maxwell" not in BILLING

    with pytest.raises(KeyError):
        adapters_mod.get_adapter("maxwell")
