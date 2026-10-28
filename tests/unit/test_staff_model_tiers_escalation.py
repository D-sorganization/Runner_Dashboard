"""Tier escalation failover (#2047 slice 5b)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from staff import adapters as adapters_mod
from staff.failover_dispatch import try_chain_failover
from staff.provider_cooldown import mark_provider_cooldown, reset_provider_cooldowns
from staff.provider_failover_config import reset_failover_config_cache
from staff.roles import RoleSpec
from staff.store import RunRecord, RunStore


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_provider_cooldowns()
    reset_failover_config_cache()

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


