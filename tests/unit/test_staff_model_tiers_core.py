"""Model tier resolution and failover integration (#2047)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from staff import adapters as adapters_mod
from staff.failover_dispatch import try_chain_failover, tried_slots_for_root
from staff.failover_model import resolve_launch_model, role_base_tier
from staff.model_resolution import resolve_staff_run_model_detail
from staff.model_tiers import (
    model_for_provider_tier,
    next_tier_up,
    reset_tier_config_cache,
    validate_role_tier,
    validate_tier_config,
)
from staff.provider_cooldown import mark_provider_cooldown, reset_provider_cooldowns
from staff.provider_failover_config import FailoverStep, reset_failover_config_cache
from staff.roles import RoleSpec
from staff.store import RunRecord, RunStore


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    reset_tier_config_cache()
    reset_failover_config_cache()
    reset_provider_cooldowns()


def test_tier_config_validates() -> None:
    assert validate_tier_config() == []


def test_tier_resolution_precedence() -> None:
    codex = adapters_mod.ADAPTERS["codex"]
    role = RoleSpec(name="w", title="W", providers=("codex",), tier="standard")
    assert (
        resolve_staff_run_model_detail(
            requested=None,
            role_model=None,
            provider_id="codex",
            adapter=codex,
            role_tier=role.tier,
        ).model
        == "gpt-6-luna"
    )
    assert (
        resolve_staff_run_model_detail(
            requested="gpt-6-luna",
            role_model=None,
            provider_id="codex",
            adapter=codex,
            role_tier=role.tier,
        ).source
        == "request"
    )
    claude = adapters_mod.ADAPTERS["claude"]
    assert model_for_provider_tier("claude", "routine") == "haiku"
    assert (
        resolve_staff_run_model_detail(
            requested=None,
            role_model=None,
            provider_id="claude",
            adapter=claude,
            role_tier="routine",
        ).model
        == "haiku"
    )


def test_unknown_tier_fails_validation() -> None:
    assert "must be one of" in validate_role_tier("turbo")[0]


def test_resolve_launch_model_rejects_unknown() -> None:
    role = RoleSpec(name="r", title="R", providers=("codex",), tier="routine")
    adapter = adapters_mod.ADAPTERS["codex"]
    with pytest.raises(ValueError, match="not valid"):
        resolve_launch_model(
            req=MagicMock(model="not-a-real-codex-model"),
            role=role,
            provider="codex",
            adapter=adapter,
            step=None,
            active_tier=role_base_tier(role),
        )


def test_next_tier_up() -> None:
    assert next_tier_up("routine") == "standard"
    assert next_tier_up("complex") is None
