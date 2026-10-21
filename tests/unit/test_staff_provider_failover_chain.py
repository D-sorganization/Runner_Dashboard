"""Fleet-wide staff provider failover chain, cooldown, and classification (#2047)."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from staff.adapter_failure import classify_adapter_output, parse_chain_summary
from staff.classifier import classify_run_failure
from staff.failover_dispatch import (
    is_failover_failure,
    parse_chain_log,
    pick_launch_step,
    pick_machine_for_chain,
    tried_providers_for_root,
    try_chain_failover,
)
from staff.provider_cooldown import (
    cooldown_snapshot,
    mark_provider_cooldown,
    provider_in_cooldown,
    reset_provider_cooldowns,
)
from staff.provider_failover_config import (
    FailoverStep,
    default_failover_chain,
    resolve_failover_chain,
    reset_failover_config_cache,
)
from staff.roles import RoleSpec
from staff.store import RunRecord, RunStore


@pytest.fixture(autouse=True)
def _reset_cooldowns() -> None:
    reset_provider_cooldowns()
    reset_failover_config_cache()


def _role(providers: tuple[str, ...]) -> RoleSpec:
    return RoleSpec(name="worker", title="Worker", providers=providers)


def test_default_chain_order_matches_repo_config() -> None:
    chain = default_failover_chain()
    assert [s.provider for s in chain][:4] == ["codex", "claude", "antigravity", "cursor-agent"]


def test_resolve_chain_filters_to_role_providers() -> None:
    role = _role(("codex", "antigravity"))
    steps = resolve_failover_chain(role)
    assert [s.provider for s in steps] == ["codex", "antigravity"]


def test_role_failover_override_must_stay_in_providers() -> None:
    role = RoleSpec(
        name="r",
        title="R",
        providers=("codex",),
        failover_chain=(FailoverStep("codex", "gpt-6-luna"),),
    )
    assert resolve_failover_chain(role) == (FailoverStep("codex", "gpt-6-luna"),)


@pytest.mark.parametrize(
    ("provider", "text", "expected"),
    [
        ("codex", "Error: Selected model is at capacity. Try again later.", "provider_capacity"),
        ("codex", "You have reached your usage limit for this billing period.", "quota_exhausted"),
        ("claude", "Error: 529 overloaded", "provider_capacity"),
        ("claude", "You have exceeded your maximum usage limit for Claude.", "quota_exhausted"),
        (
            "antigravity",
            "Error: RESOURCE_EXHAUSTED: quota exceeded for quota metric 'Queries'",
            "quota_exhausted",
        ),
        ("cursor-agent", "Usage limit reached for this account.", "quota_exhausted"),
    ],
)
def test_adapter_classification_fixtures(provider: str, text: str, expected: str) -> None:
    hint = classify_adapter_output(provider, text)
    assert hint is not None
    assert hint.failure_class == expected
    diag = classify_run_failure(provider=provider, exit_code=1, output_text=text)
    assert diag.failure_class == expected


def test_no_failover_on_task_failure() -> None:
    diag = classify_run_failure("codex", 1, output_text="STAFF_RESULT: tests failed in test_foo.py")
    assert not is_failover_failure(diag.failure_class)


def test_cooldown_expires() -> None:
    mark_provider_cooldown("codex", "provider_capacity", message="retry after 1 seconds")
    assert provider_in_cooldown("codex")
    time.sleep(1.1)
    assert not provider_in_cooldown("codex")


def test_pick_launch_step_skips_cooling_provider() -> None:
    role = _role(("codex", "claude"))
    adapters = {
        "codex": MagicMock(installed=lambda: True, unattended=True, executable="codex"),
        "claude": MagicMock(installed=lambda: True, unattended=True, executable="claude"),
    }
    mark_provider_cooldown("codex", "provider_capacity")
    chain = (FailoverStep("codex"), FailoverStep("claude"))
    step = pick_launch_step(chain, adapters, role)
    assert step is not None
    assert step.provider == "claude"


def test_pick_machine_for_chain_prefers_node_with_healthy_provider() -> None:
    board = {
        "machines": {
            "Brick": {
                "status": "online",
                "providers": {"codex": True, "claude": True},
                "providers_cooldown": [{"provider": "codex"}],
                "running": [],
                "queued": [],
            },
            "OGLaptop": {
                "status": "online",
                "providers": {"codex": True, "claude": True},
                "providers_cooldown": [],
                "running": [],
                "queued": [],
            },
        }
    }
    chain = (FailoverStep("codex"), FailoverStep("claude"))
    assert pick_machine_for_chain(board, chain, "DeskComputer") == "OGLaptop"


def test_chain_summary_brief() -> None:
    attempts = [
        {"provider": "codex", "outcome": "provider_capacity"},
        {"provider": "claude", "outcome": "succeeded"},
    ]
    assert "codex" in parse_chain_summary(attempts).lower()
    assert "claude" in parse_chain_summary(attempts).lower()


def test_cooldown_snapshot_serializable() -> None:
    mark_provider_cooldown("claude", "quota_exhausted")
    snap = cooldown_snapshot()
    assert snap and snap[0]["provider"] == "claude"
    json.dumps(snap)


