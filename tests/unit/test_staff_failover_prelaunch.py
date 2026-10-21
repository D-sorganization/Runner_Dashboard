"""Pre-launch failover pick (#2047 slice 3b)."""

from __future__ import annotations

from unittest.mock import MagicMock

from staff.classifier import classify_run_failure
from staff.failover_dispatch import is_failover_failure, pick_launch_step, pick_machine_for_chain
from staff.provider_cooldown import mark_provider_cooldown, reset_provider_cooldowns
from staff.provider_failover_config import FailoverStep, reset_failover_config_cache
from staff.roles import RoleSpec

import pytest


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_provider_cooldowns()
    reset_failover_config_cache()


def _role(providers: tuple[str, ...]) -> RoleSpec:
    return RoleSpec(name="worker", title="Worker", providers=providers)


def test_no_failover_on_task_failure() -> None:
    diag = classify_run_failure("codex", 1, output_text="STAFF_RESULT: tests failed in test_foo.py")
    assert not is_failover_failure(diag.failure_class)


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
