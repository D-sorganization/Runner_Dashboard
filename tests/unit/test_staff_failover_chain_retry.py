"""Chain failover retry scheduling (#2047 slice 4a)."""

from __future__ import annotations

from unittest.mock import MagicMock

from staff.adapter_failure import parse_chain_summary
from staff.failover_dispatch import (
    parse_chain_log,
    tried_providers_for_root,
    try_chain_failover,
)
from staff.provider_failover_config import reset_failover_config_cache
from staff.provider_cooldown import reset_provider_cooldowns
from staff.roles import RoleSpec
from staff.store import RunRecord, RunStore

import pytest


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_provider_cooldowns()
    reset_failover_config_cache()


def _role(providers: tuple[str, ...]) -> RoleSpec:
    return RoleSpec(name="worker", title="Worker", providers=providers)


def test_chain_failover_schedules_next_provider(tmp_path) -> None:
    store = RunStore(path=tmp_path / "runs.sqlite3")
    root = RunRecord(
        id="run-root",
        role="worker",
        provider="codex",
        model="gpt-6-luna",
        machine="test",
        repo="",
        target_kind="prompt",
        target_ref="",
        prompt="do work",
        status="failed",
        failure_class="provider_capacity",
        attempt=1,
        max_attempts=2,
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
        prompt="do work",
        branch="staff/worker-task",
        lease_ritual=False,
        consolidation=None,
        focus="",
        head_ref="",
        strategy_mode="",
    )
    role = _role(("codex", "claude"))
    runner = MagicMock()
    runner.store = store
    runner.machine = "test"
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
            build_command=lambda *a, **k: ["claude"],
        ),
    }
    guard = MagicMock()
    guard.can_run.return_value = (True, "")
    latest = store.get_run("run-root")
    assert latest is not None
    scheduled = try_chain_failover(runner, latest, plan, role, guard, retries_exhausted=False)
    assert scheduled is True
    assert store.get_run("run-root-a2") is not None
    chain = parse_chain_log(store.get_run("run-root").provider_chain)
    assert chain[0]["provider"] == "codex"
    assert chain[0]["outcome"] == "provider_capacity"


def test_chain_summary_brief() -> None:
    attempts = [
        {"provider": "codex", "outcome": "provider_capacity"},
        {"provider": "claude", "outcome": "succeeded"},
    ]
    assert "codex" in parse_chain_summary(attempts).lower()
    assert "claude" in parse_chain_summary(attempts).lower()


def test_tried_providers_includes_retry_attempts(tmp_path) -> None:
    store = RunStore(path=tmp_path / "tried.sqlite3")
    store.create_run(
        RunRecord(
            id="run-a",
            role="w",
            provider="codex",
            model="m",
            machine="t",
            repo="",
            target_kind="",
            target_ref="",
            prompt="",
        )
    )
    store.create_run(
        RunRecord(
            id="run-a-a2",
            role="w",
            provider="claude",
            model="m",
            machine="t",
            repo="",
            target_kind="",
            target_ref="",
            prompt="",
            retry_of="run-a",
            attempt=2,
        )
    )
    assert tried_providers_for_root(store, "run-a") == {"codex", "claude"}
