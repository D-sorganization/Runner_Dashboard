"""Model tier resolution and failover integration (#2047)."""

from __future__ import annotations

import pytest
from staff import adapters as adapters_mod
from staff.failover_model import resolve_launch_model, role_base_tier
from staff.model_resolution import resolve_staff_run_model_detail
from staff.model_tiers import (
    model_for_provider_tier,
    next_tier_up,
    reset_tier_config_cache,
    validate_role_tier,
    validate_tier_config,
)
from staff.plan import RunRequest
from staff.provider_cooldown import reset_provider_cooldowns
from staff.provider_failover_config import FailoverStep, reset_failover_config_cache
from staff.roles import RoleSpec

pytestmark = pytest.mark.unit


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
            req=RunRequest(role="r", model="not-a-real-codex-model"),
            role=role,
            provider="codex",
            adapter=adapter,
            step=None,
            active_tier=role_base_tier(role),
        )


def test_launch_fallback_uses_target_provider_tier_not_primary_override() -> None:
    role = RoleSpec(name="r", title="R", providers=("codex", "claude"), tier="routine")
    req = RunRequest(role="r", provider="codex", model="gpt-6-luna", model_tier="complex")
    model, tier = resolve_launch_model(
        req=req,
        role=role,
        provider="claude",
        adapter=adapters_mod.ADAPTERS["claude"],
        step=FailoverStep("claude"),
        active_tier="standard",
    )
    assert (model, tier) == (model_for_provider_tier("claude", "complex"), "complex")


def test_higher_internal_tier_overrides_lower_tier_failover_step_model() -> None:
    role = RoleSpec(name="r", title="R", providers=("codex", "claude"), tier="routine")
    req = RunRequest(role="r", provider="codex", model_tier="complex")
    model, tier = resolve_launch_model(
        req=req,
        role=role,
        provider="claude",
        adapter=adapters_mod.ADAPTERS["claude"],
        step=FailoverStep("claude", "haiku"),
        active_tier="routine",
    )
    assert (model, tier) == (model_for_provider_tier("claude", "complex"), "complex")


def test_launch_fallback_still_rejects_invalid_model_for_requested_primary() -> None:
    role = RoleSpec(name="r", title="R", providers=("codex", "claude"), tier="routine")
    req = RunRequest(role="r", provider="codex", model="sonnet")
    with pytest.raises(ValueError, match="not valid for provider 'codex'"):
        resolve_launch_model(
            req=req,
            role=role,
            provider="claude",
            adapter=adapters_mod.ADAPTERS["claude"],
            step=FailoverStep("claude"),
            active_tier="routine",
        )


def test_launch_primary_keeps_explicit_model() -> None:
    role = RoleSpec(name="r", title="R", providers=("codex",), tier="routine")
    model, tier = resolve_launch_model(
        req=RunRequest(role="r", provider="codex", model="gpt-6-luna"),
        role=role,
        provider="codex",
        adapter=adapters_mod.ADAPTERS["codex"],
        step=FailoverStep("codex"),
        active_tier="routine",
    )
    assert (model, tier) == ("gpt-6-luna", "routine")


def test_codex_gpt_6_1_terra_allowed_for_staff_dispatch() -> None:
    """Codex CLI 0.164+ accepts gpt-6.1-terra; staff must not 422 it (#3532)."""
    role = RoleSpec(name="r", title="R", providers=("codex",), tier="routine")
    model, tier = resolve_launch_model(
        req=RunRequest(role="r", provider="codex", model="gpt-6.1-terra"),
        role=role,
        provider="codex",
        adapter=adapters_mod.ADAPTERS["codex"],
        step=FailoverStep("codex"),
        active_tier="routine",
    )
    assert (model, tier) == ("gpt-6.1-terra", "routine")
    argv = adapters_mod.ADAPTERS["codex"].build_command("hi", "/tmp", model="gpt-6.1-terra")
    assert argv[argv.index("--model") + 1] == "gpt-6.1-terra"


def test_launch_fallback_uses_configured_primary_when_provider_is_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    from staff import provider_failover_config

    monkeypatch.setattr(
        provider_failover_config,
        "default_failover_chain",
        lambda: (FailoverStep("claude"), FailoverStep("codex")),
    )
    role = RoleSpec(name="r", title="R", providers=("claude", "codex"), tier="routine")
    model, tier = resolve_launch_model(
        req=RunRequest(role="r", model="opus"),
        role=role,
        provider="codex",
        adapter=adapters_mod.ADAPTERS["codex"],
        step=FailoverStep("codex"),
        active_tier="routine",
    )
    assert (model, tier) == (model_for_provider_tier("codex", "routine"), "routine")


def test_fallback_does_not_reuse_primary_role_model() -> None:
    role = RoleSpec(name="r", title="R", providers=("codex", "claude"), tier="routine", model="gpt-6-luna")
    model, tier = resolve_launch_model(
        req=RunRequest(role="r", provider="codex"),
        role=role,
        provider="claude",
        adapter=adapters_mod.ADAPTERS["claude"],
        step=FailoverStep("claude"),
        active_tier="routine",
    )
    assert (model, tier) == (model_for_provider_tier("claude", "routine"), "routine")


def test_run_request_validates_optional_internal_model_tier() -> None:
    assert RunRequest(role="r").model_tier in (None, "")
    assert RunRequest(role="r", model_tier="complex").model_tier == "complex"
    with pytest.raises(ValueError, match="model_tier"):
        RunRequest(role="r", model_tier="turbo")


def test_next_tier_up() -> None:
    assert next_tier_up("routine") == "standard"
    assert next_tier_up("complex") is None
