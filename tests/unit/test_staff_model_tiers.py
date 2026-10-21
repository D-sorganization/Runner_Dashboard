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


def test_same_tier_failover_uses_tier_model(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("staff.failover_dispatch.can_run_unattended", lambda *a, **k: True)
    store = RunStore(path=tmp_path / "tier-fail.sqlite3")
    root = RunRecord(
        id="run-tier",
        role="worker",
        provider="codex",
        model="gpt-6-luna",
        model_tier="routine",
        machine="test",
        repo="",
        target_kind="prompt",
        target_ref="",
        prompt="x",
        status="failed",
        failure_class="provider_capacity",
        attempt=1,
        max_attempts=3,
    )
    store.create_run(root)
    plan = MagicMock(
        role="worker",
        provider="codex",
        model="gpt-6-luna",
        repo="",
        target_kind="prompt",
        target_ref="",
        operator_prompt="",
        prompt="x",
        branch="staff/worker-task",
        lease_ritual=False,
        consolidation=None,
        focus="",
        head_ref="",
        strategy_mode="",
    )
    role = RoleSpec(name="worker", title="Worker", providers=("codex", "claude"), tier="routine")
    runner = MagicMock(store=store, machine="test")
    runner._adapters = {
        "codex": MagicMock(
            installed=lambda: True,
            unattended=True,
            executable="codex",
            build_command=lambda *a, **k: ["codex"],
        ),
        "claude": MagicMock(
            installed=lambda: True,
            unattended=True,
            executable="claude",
            default_model="sonnet",
            models=(),
            build_command=lambda *a, **k: ["claude"],
        ),
    }
    guard = MagicMock()
    guard.can_run.return_value = (True, "")
    latest = store.get_run("run-tier")
    assert try_chain_failover(runner, latest, plan, role, guard, retries_exhausted=False)
    attempt = store.get_run("run-tier-a2")
    assert attempt is not None
    assert attempt.provider == "claude"
    assert attempt.model == "haiku"
    assert attempt.model_tier == "routine"


def test_tier_escalation_requires_flag(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("staff.failover_dispatch.can_run_unattended", lambda *a, **k: True)
    store = RunStore(path=tmp_path / "escalate.sqlite3")
    root = RunRecord(
        id="run-esc",
        role="worker",
        provider="claude",
        model="haiku",
        model_tier="routine",
        machine="t",
        repo="",
        target_kind="",
        target_ref="",
        prompt="",
        status="failed",
        failure_class="provider_capacity",
        attempt=1,
        max_attempts=2,
    )
    store.create_run(root)
    plan = MagicMock(
        role="worker",
        provider="claude",
        model="haiku",
        repo="",
        target_kind="",
        target_ref="",
        operator_prompt="",
        prompt="",
        branch="b",
        lease_ritual=False,
        consolidation=None,
        focus="",
        head_ref="",
        strategy_mode="",
    )
    role = RoleSpec(
        name="worker",
        title="W",
        providers=("claude",),
        tier="routine",
        allow_tier_escalation=False,
    )
    real_claude = adapters_mod.ADAPTERS["claude"]
    claude = MagicMock(
        installed=lambda: True,
        unattended=True,
        executable=real_claude.executable,
        default_model=real_claude.default_model,
        models=real_claude.models,
        build_command=real_claude.build_command,
    )
    runner = MagicMock(store=store, machine="t", _adapters={"claude": claude})
    guard = MagicMock()
    guard.can_run.return_value = (True, "")
    try_chain_failover(runner, store.get_run("run-esc"), plan, role, guard, retries_exhausted=False)
    assert store.get_run("run-esc").status == "blocked"
    reset_provider_cooldowns()

    role_esc = RoleSpec(
        name="worker",
        title="W",
        providers=("codex", "claude"),
        tier="routine",
        allow_tier_escalation=True,
    )
    real_codex = adapters_mod.ADAPTERS["codex"]
    codex = MagicMock(
        installed=lambda: True,
        unattended=True,
        executable=real_codex.executable,
        default_model=real_codex.default_model,
        models=real_codex.models,
        build_command=real_codex.build_command,
    )
    runner._adapters = {"codex": codex, "claude": claude}
    root2 = RunRecord(
        id="run-esc2",
        role="worker",
        provider="claude",
        model="haiku",
        model_tier="routine",
        machine="t",
        repo="",
        target_kind="",
        target_ref="",
        prompt="",
        status="failed",
        failure_class="provider_capacity",
        attempt=1,
        max_attempts=2,
        provider_chain='[{"provider":"codex","model":"gpt-6-luna","tier":"routine","outcome":"provider_capacity"}]',
    )
    store.create_run(root2)
    plan2 = SimpleNamespace(
        role="worker",
        provider="claude",
        model="haiku",
        repo="",
        target_kind="",
        target_ref="",
        operator_prompt="",
        prompt="retry",
        branch="b2",
        lease_ritual=False,
        consolidation=None,
        focus="",
        head_ref="",
        strategy_mode="",
    )
    try_chain_failover(runner, store.get_run("run-esc2"), plan2, role_esc, guard, retries_exhausted=False)
    attempt = store.get_run("run-esc2-a2")
    assert attempt is not None
    assert attempt.model_tier == "standard"
    assert attempt.provider == "codex"


def test_tier_escalation_does_not_bypass_provider_cooldown(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Escalation picks a higher model tier, not a healthier provider."""
    monkeypatch.setattr("staff.failover_dispatch.can_run_unattended", lambda *a, **k: True)
    mark_provider_cooldown("claude", "provider_capacity")
    store = RunStore(path=tmp_path / "cooldown-esc.sqlite3")
    root = RunRecord(
        id="run-cool-esc",
        role="worker",
        provider="claude",
        model="haiku",
        model_tier="routine",
        machine="t",
        repo="",
        target_kind="",
        target_ref="",
        prompt="",
        status="failed",
        failure_class="provider_capacity",
        attempt=1,
        max_attempts=2,
    )
    store.create_run(root)
    plan = SimpleNamespace(
        role="worker",
        provider="claude",
        model="haiku",
        repo="",
        target_kind="",
        target_ref="",
        operator_prompt="",
        prompt="",
        branch="b",
        lease_ritual=False,
        consolidation=None,
        focus="",
        head_ref="",
        strategy_mode="",
    )
    role = RoleSpec(
        name="worker",
        title="W",
        providers=("claude",),
        tier="routine",
        allow_tier_escalation=True,
    )
    real_claude = adapters_mod.ADAPTERS["claude"]
    claude = MagicMock(
        installed=lambda: True,
        unattended=True,
        executable=real_claude.executable,
        default_model=real_claude.default_model,
        models=real_claude.models,
        build_command=real_claude.build_command,
    )
    runner = MagicMock(store=store, machine="t", _adapters={"claude": claude})
    guard = MagicMock()
    guard.can_run.return_value = (True, "")
    try_chain_failover(runner, store.get_run("run-cool-esc"), plan, role, guard, retries_exhausted=False)
    assert store.get_run("run-cool-esc-a2") is None
    assert store.get_run("run-cool-esc").status == "blocked"
    assert store.get_run("run-cool-esc").failure_class == "providers_exhausted"


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
