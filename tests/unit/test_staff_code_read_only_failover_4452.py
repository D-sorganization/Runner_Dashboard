"""Read-only roles: explicit-provider failover substitution (#4452)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from staff.adapters import ProviderAdapter
from staff.workspace import READ_ONLY_FLEET_RULES
from staff.failover_dispatch import ProviderPick, resolve_provider_and_model
from staff.plan import RunRequest
from staff.provider_failover_config import FailoverStep
from staff.read_only_failover import (
    align_role_read_only_authority,
    code_read_only_chain_warnings,
    explain_read_only_chain_exhausted,
    role_read_only_config_warnings,
)
from staff.roles import RoleSpec

pytestmark = pytest.mark.unit


def _adapter(provider: str, *, installed: bool = True, default_model: str | None = None) -> MagicMock:
    defaults = {
        "codex": "gpt-6.1-sol",
        "claude": "claude-opus-5-5",
        "antigravity": "gemini-3.8-flash-high",
    }
    return MagicMock(
        spec=ProviderAdapter,
        provider_id=provider,
        default_model=default_model or defaults.get(provider),
        models=(),
        unattended=True,
        installed=lambda: installed,
    )


def _code_reviewer_role() -> RoleSpec:
    return RoleSpec(
        name="code-reviewer",
        title="Code Reviewer",
        providers=("codex", "claude", "cursor-agent"),
        model="gpt-6.1-sol",
        permissions={"code_read_only": True, "push_branch": False, "open_pr": False},
        failover_chain=(
            FailoverStep("codex", "gpt-6.1-sol"),
            FailoverStep("claude", "claude-opus-5-5"),
            FailoverStep("cursor-agent"),
        ),
    )


@pytest.fixture(autouse=True)
def _no_auth_or_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    from staff import failover_dispatch

    monkeypatch.setattr(failover_dispatch, "_provider_auth_blocks", lambda provider, machine: False)
    monkeypatch.setattr(failover_dispatch, "provider_in_cooldown", lambda provider: False)
    monkeypatch.setattr(failover_dispatch.quota_mod, "ceiling_percent", lambda value: None)
    monkeypatch.setattr(failover_dispatch.quota_mod, "headroom", lambda provider, ceiling: (True, ""))


def test_explicit_non_read_only_provider_substitutes_to_chain_step() -> None:
    role = _code_reviewer_role()
    adapters = {
        "codex": _adapter("codex"),
        "claude": _adapter("claude"),
        "cursor-agent": _adapter("cursor-agent"),
    }
    pick = resolve_provider_and_model(
        adapters,
        "hub",
        RunRequest(role="code-reviewer", provider="codex", prompt="review"),
        role,
    )
    assert isinstance(pick, ProviderPick)
    assert pick.provider == "claude"
    assert pick.requested_provider == "codex"
    assert pick.substitution_reason == "code_read_only"


def test_strict_provider_preserves_422() -> None:
    role = _code_reviewer_role()
    adapters = {"codex": _adapter("codex"), "claude": _adapter("claude")}
    with pytest.raises(ValueError, match="runs code-read-only; provider 'codex'"):
        resolve_provider_and_model(
            adapters,
            "hub",
            RunRequest(role="code-reviewer", provider="codex", prompt="review", strict_provider=True),
            role,
        )


def test_chain_exhaustion_lists_skipped_providers() -> None:
    role = _code_reviewer_role()
    adapters = {
        "codex": _adapter("codex", installed=False),
        "claude": _adapter("claude", installed=False),
        "cursor-agent": _adapter("cursor-agent", installed=False),
    }
    req = RunRequest(role="code-reviewer", provider="codex", prompt="review")
    with pytest.raises(ValueError, match="no read-only-capable provider"):
        resolve_provider_and_model(adapters, "hub", req, role)
    msg = explain_read_only_chain_exhausted(adapters, role, req, machine="hub", skip_providers=None)
    assert "codex:" in msg and "claude:" in msg


def test_private_repo_skips_antigravity_for_restriction() -> None:
    role = RoleSpec(
        name="code-reviewer",
        title="Reviewer",
        providers=("codex", "claude", "antigravity"),
        permissions={"code_read_only": True, "push_branch": False, "open_pr": False},
        failover_chain=(
            FailoverStep("codex"),
            FailoverStep("antigravity"),
            FailoverStep("claude", "claude-opus-5-5"),
        ),
    )
    adapters = {"codex": _adapter("codex"), "claude": _adapter("claude"), "antigravity": _adapter("antigravity")}
    pick = resolve_provider_and_model(
        adapters,
        "hub",
        RunRequest(role="code-reviewer", provider="codex", repo="Tools_Private", prompt="review"),
        role,
    )
    assert pick.provider == "claude"


def test_step_model_used_when_not_in_role_gpt_list() -> None:
    role = _code_reviewer_role()
    adapters = {"codex": _adapter("codex"), "claude": _adapter("claude")}
    pick = resolve_provider_and_model(
        adapters,
        "hub",
        RunRequest(role="code-reviewer", provider="codex", prompt="review"),
        role,
    )
    assert pick.model == "claude-opus-5-5"


def test_substituted_plan_still_composes_read_only_prompt(tmp_path: Path, staff_cli_installed) -> None:
    from staff.runner import StaffRunner
    from staff.store import RunStore

    staff_cli_installed("claude")
    role = _code_reviewer_role()
    runner = StaffRunner(
        store=RunStore(tmp_path / "runs.sqlite3"),
        roles_loader=lambda: {"code-reviewer": role},
    )
    plan = runner.plan(RunRequest(role="code-reviewer", provider="codex", prompt="review only"))
    assert plan.provider == "claude"
    assert plan.requested_provider == "codex"
    assert READ_ONLY_FLEET_RULES in plan.prompt


def test_validator_warns_non_read_only_first_chain_step() -> None:
    yml = {
        "name": "code-reviewer",
        "title": "Reviewer",
        "providers": ["codex", "claude"],
        "permissions": {
            "code_read_only": True,
            "push_branch": False,
            "open_pr": False,
            "merge": False,
            "lease": False,
            "notify_user": False,
            "library_read": False,
            "host_shell": False,
        },
        "budget": {"usd_per_run": 0, "usd_per_day": 0, "max_minutes": 1, "max_window_percent": 10},
        "surface": "dashboard",
        "retry": {"failover_chain": [{"provider": "codex"}, {"provider": "claude", "model": "claude-opus-5-5"}]},
    }
    warnings = code_read_only_chain_warnings(yml)
    assert any("failover_chain[0]" in w for w in warnings)


def test_role_health_style_warnings_from_role_spec() -> None:
    role = _code_reviewer_role()
    assert any("failover_chain[0]" in w for w in role_read_only_config_warnings(role))


def test_authority_alignment_forces_read_only_on_stale_role_copy() -> None:
    stale = RoleSpec(
        name="code-reviewer",
        title="Reviewer",
        permissions={"push_branch": True, "open_pr": True},
    )
    aligned = align_role_read_only_authority(
        stale,
        RunRequest(role="code-reviewer", prompt="x", authority_code_read_only=True),
    )
    assert aligned.code_read_only is True
    assert stale.code_read_only is False


def test_cross_provider_skips_author_when_pr_set() -> None:
    role = _code_reviewer_role()
    adapters = {
        "codex": _adapter("codex"),
        "claude": _adapter("claude"),
        "cursor-agent": _adapter("cursor-agent", installed=False),
    }
    req = RunRequest(role="code-reviewer", provider="codex", repo="Runner_Dashboard", pr=42, prompt="review")
    with patch("staff.review.detect_author_provider", return_value="claude"):
        with pytest.raises(ValueError, match="no read-only-capable provider"):
            resolve_provider_and_model(adapters, "hub", req, role)


def test_stale_forwarded_role_still_gets_read_only_prompt(tmp_path: Path, staff_cli_installed) -> None:
    from staff.runner import StaffRunner
    from staff.store import RunStore

    staff_cli_installed("claude")
    stale = RoleSpec(
        name="code-reviewer",
        title="Reviewer",
        providers=("claude", "codex"),
        permissions={"push_branch": True, "open_pr": True},
        failover_chain=(FailoverStep("claude"), FailoverStep("codex")),
    )
    runner = StaffRunner(
        store=RunStore(tmp_path / "runs.sqlite3"),
        roles_loader=lambda: {"code-reviewer": stale},
    )
    plan = runner.plan(
        RunRequest(
            role="code-reviewer",
            provider="claude",
            prompt="review",
            authority_code_read_only=True,
        )
    )
    assert READ_ONLY_FLEET_RULES in plan.prompt