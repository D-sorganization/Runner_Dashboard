"""Pre-launch failover pick (#2047 slice 3b)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from staff.adapters import ProviderAdapter
from staff.classifier import classify_run_failure
from staff.failover_dispatch import (
    is_failover_failure,
    pick_launch_step,
    pick_machine_for_chain,
    resolve_provider_and_model,
)
from staff.provider_cooldown import mark_provider_cooldown, reset_provider_cooldowns
from staff.provider_failover_config import FailoverStep, reset_failover_config_cache
from staff.roles import RoleSpec

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_provider_cooldowns()
    reset_failover_config_cache()


def _role(providers: tuple[str, ...]) -> RoleSpec:
    return RoleSpec(name="worker", title="Worker", providers=providers)


def _adapter(provider: str, *, installed: bool) -> MagicMock:
    """A fully specified adapter double with deterministic install and model metadata."""
    defaults = {"codex": "gpt-6-luna", "claude": "sonnet", "antigravity": "gemini-3.8-flash-high"}
    return MagicMock(
        spec=ProviderAdapter,
        provider_id=provider,
        label=provider,
        executable=f"{provider}-test-cli",
        argv=("-p", "{prompt}"),
        default_model=defaults.get(provider),
        models=(),
        prompt_via_stdin=False,
        json_lines=True,
        max_concurrency=1,
        notes="test adapter",
        extra_env={},
        env_builder=None,
        lease_as=None,
        unattended=True,
        policy_text=None,
        chat_json_lines=None,
        installed=lambda: installed,
    )


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


def test_explicit_uninstalled_primary_uses_next_installed_allowed_chain_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from staff import failover_dispatch
    from staff.plan import RunRequest

    role = _role(("codex", "claude"))
    adapters = {
        "codex": _adapter("codex", installed=False),
        "claude": _adapter("claude", installed=True),
    }
    monkeypatch.setattr(failover_dispatch, "_provider_auth_blocks", lambda provider, machine: False)
    monkeypatch.setattr(failover_dispatch, "provider_in_cooldown", lambda provider: False)
    monkeypatch.setattr(failover_dispatch.quota_mod, "ceiling_percent", lambda value: None)
    monkeypatch.setattr(failover_dispatch.quota_mod, "headroom", lambda provider, ceiling: (True, ""))

    provider, model, tier, _ = resolve_provider_and_model(
        adapters,
        "local",
        RunRequest(role="worker", provider="codex", model_tier="standard", prompt="task"),
        role,
    )

    assert provider == "claude"
    assert model == "sonnet"
    assert tier == "standard"


def test_explicit_primary_with_unreadable_install_state_uses_next_chain_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from staff import failover_dispatch
    from staff.plan import RunRequest

    role = _role(("codex", "claude"))

    def unavailable() -> bool:
        raise RuntimeError("PATH probe failed")

    adapters = {
        "codex": _adapter("codex", installed=False),
        "claude": _adapter("claude", installed=True),
    }
    adapters["codex"].installed = unavailable
    monkeypatch.setattr(failover_dispatch, "_provider_auth_blocks", lambda provider, machine: False)
    monkeypatch.setattr(failover_dispatch, "provider_in_cooldown", lambda provider: False)
    monkeypatch.setattr(failover_dispatch.quota_mod, "ceiling_percent", lambda value: None)
    monkeypatch.setattr(failover_dispatch.quota_mod, "headroom", lambda provider, ceiling: (True, ""))

    provider, _, _, _ = resolve_provider_and_model(
        adapters,
        "local",
        RunRequest(role="worker", provider="codex", prompt="task"),
        role,
    )

    assert provider == "claude"


def test_explicit_cooling_primary_uses_next_chain_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from staff import failover_dispatch
    from staff.plan import RunRequest

    role = _role(("codex", "claude"))
    adapters = {
        "codex": _adapter("codex", installed=True),
        "claude": _adapter("claude", installed=True),
    }
    monkeypatch.setattr(failover_dispatch, "_provider_auth_blocks", lambda provider, machine: False)
    monkeypatch.setattr(failover_dispatch.quota_mod, "ceiling_percent", lambda value: None)
    monkeypatch.setattr(failover_dispatch.quota_mod, "headroom", lambda provider, ceiling: (True, ""))
    mark_provider_cooldown("codex", "provider_capacity")

    provider, _, _, _ = resolve_provider_and_model(
        adapters,
        "local",
        RunRequest(role="worker", provider="codex", prompt="task"),
        role,
    )

    assert provider == "claude"


def test_no_installed_provider_in_explicit_chain_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from staff import failover_dispatch
    from staff.plan import RunRequest

    role = _role(("codex", "claude"))
    adapters = {
        "codex": _adapter("codex", installed=False),
        "claude": _adapter("claude", installed=False),
    }
    monkeypatch.setattr(failover_dispatch, "_provider_auth_blocks", lambda provider, machine: False)
    monkeypatch.setattr(failover_dispatch, "provider_in_cooldown", lambda provider: False)
    monkeypatch.setattr(failover_dispatch.quota_mod, "ceiling_percent", lambda value: None)
    monkeypatch.setattr(failover_dispatch.quota_mod, "headroom", lambda provider, ceiling: (True, ""))

    with pytest.raises(ValueError, match="no provider available in failover chain"):
        resolve_provider_and_model(
            adapters,
            "local",
            RunRequest(role="worker", provider="codex", prompt="task"),
            role,
        )


def test_explicit_missing_primary_does_not_select_disabled_chain_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from staff import failover_dispatch, runner_ops
    from staff.plan import RunRequest

    role = _role(("codex", "claude"))
    adapters = {
        "codex": _adapter("codex", installed=False),
        "claude": _adapter("claude", installed=True),
    }
    monkeypatch.setattr(failover_dispatch, "_provider_auth_blocks", lambda provider, machine: False)
    monkeypatch.setattr(failover_dispatch, "provider_in_cooldown", lambda provider: False)
    monkeypatch.setattr(failover_dispatch.quota_mod, "ceiling_percent", lambda value: None)
    monkeypatch.setattr(failover_dispatch.quota_mod, "headroom", lambda provider, ceiling: (True, ""))
    monkeypatch.setattr(runner_ops.provider_switch, "is_disabled", lambda provider: provider == "claude")

    with pytest.raises(ValueError, match="no provider available in failover chain"):
        resolve_provider_and_model(
            adapters,
            "local",
            RunRequest(role="worker", provider="codex", prompt="task"),
            role,
        )


@pytest.mark.parametrize(
    ("provider", "adapter_installed", "adapter_unattended", "role_providers", "error"),
    [
        ("missing", False, True, ("codex", "claude"), "unknown provider"),
        ("gemini", False, True, ("codex", "claude"), "not allowed"),
        ("codex", True, False, ("codex", "claude"), "chat-only"),
    ],
)
def test_explicit_unknown_disallowed_or_chat_only_provider_stays_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    adapter_installed: bool,
    adapter_unattended: bool,
    role_providers: tuple[str, ...],
    error: str,
) -> None:
    from staff import failover_dispatch
    from staff.plan import RunRequest

    role = _role(role_providers)
    adapters = {
        "codex": _adapter("codex", installed=adapter_installed),
        "claude": _adapter("claude", installed=True),
        "gemini": _adapter("gemini", installed=True),
    }
    adapters["codex"].unattended = adapter_unattended
    monkeypatch.setattr(failover_dispatch, "_provider_auth_blocks", lambda candidate, machine: False)
    monkeypatch.setattr(failover_dispatch, "provider_in_cooldown", lambda candidate: False)
    monkeypatch.setattr(failover_dispatch.quota_mod, "ceiling_percent", lambda value: None)
    monkeypatch.setattr(failover_dispatch.quota_mod, "headroom", lambda candidate, ceiling: (True, ""))

    with pytest.raises(ValueError, match=error):
        resolve_provider_and_model(
            adapters,
            "local",
            RunRequest(role="worker", provider=provider, prompt="task"),
            role,
        )


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
