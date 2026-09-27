"""One node switch turns a provider off on every dispatch path (#1597).

``STAFF_DISABLED_PROVIDERS`` (comma-separated staff or dashboard ids) used to be
honoured only by staff chat. The owner turned Gemini off (2026-09-26) because the
Gemini CLI may bill per use; these tests pin that staff runs, the budget gate,
retries and the registry probes all respect the same switch.
"""

from __future__ import annotations

from pathlib import Path

import provider_switch
import pytest
from agent_remediation import provider_probe, providers
from staff import budget as budget_mod
from staff import retry as retry_mod
from staff.availability import is_provider_healthy
from staff.plan import RunRequest
from staff.roles import RoleSpec
from staff.runner import StaffRunner
from staff.runner_ops import can_run_unattended
from staff.store import RunStore


@pytest.fixture
def gemini_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", " Gemini , ollama")


# ── the switch itself ────────────────────────────────────────────────────


@pytest.mark.unit
def test_switch_is_empty_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAFF_DISABLED_PROVIDERS", raising=False)
    assert provider_switch.disabled_providers() == frozenset()
    assert not provider_switch.is_disabled("gemini")


@pytest.mark.unit
@pytest.mark.usefixtures("gemini_off")
@pytest.mark.parametrize("pid", ["gemini", "gemini_cli", "gemini-cli", "GEMINI_CLI", "ollama"])
def test_staff_dashboard_and_conductor_ids_all_match(pid: str) -> None:
    assert provider_switch.is_disabled(pid)


@pytest.mark.unit
@pytest.mark.usefixtures("gemini_off")
@pytest.mark.parametrize("pid", ["claude", "claude_code_cli", "codex", "codex_cli", "antigravity", ""])
def test_other_providers_stay_on(pid: str) -> None:
    assert not provider_switch.is_disabled(pid)


@pytest.mark.unit
def test_claude_matches_its_dashboard_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "claude")
    assert provider_switch.is_disabled("claude_code_cli")
    assert not provider_switch.is_disabled("claude-ollama")


# ── staff runs ───────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.usefixtures("gemini_off")
def test_runner_never_picks_or_accepts_a_disabled_provider(tmp_path: Path) -> None:
    role = RoleSpec(name="r", title="R", providers=("gemini", "codex"), surface="dashboard")
    runner = StaffRunner(store=RunStore(tmp_path / "s.db"), roles_loader=lambda: {"r": role})
    assert runner.plan(RunRequest(role="r", prompt="go")).provider == "codex"
    with pytest.raises(ValueError, match="disabled on this node"):
        runner.plan(RunRequest(role="r", provider="gemini", prompt="go"))


@pytest.mark.unit
@pytest.mark.usefixtures("gemini_off")
def test_unattended_check_and_retry_skip_disabled_providers() -> None:
    from staff.adapters import ADAPTERS

    assert not can_run_unattended(ADAPTERS, "gemini")
    assert can_run_unattended(ADAPTERS, "codex")
    role = RoleSpec(name="r", title="R", providers=("claude", "gemini", "codex"))
    usable = lambda pid: can_run_unattended(ADAPTERS, pid)  # noqa: E731
    assert retry_mod.next_fallback_provider(role, "claude", usable=usable) == "codex"


@pytest.mark.unit
@pytest.mark.usefixtures("gemini_off")
def test_budget_gate_ignores_disabled_providers(tmp_path: Path) -> None:
    """A disabled provider with no quota data must not keep a role due."""
    guard = budget_mod.BudgetGuard(
        RunStore(tmp_path / "s.db"), quota_check=lambda p, c: (p != "claude", f"{p}: checked")
    )
    role = RoleSpec(name="r", title="R", providers=("claude", "gemini"))
    ok, reason = guard.can_run(role)
    assert not ok and reason.startswith("quota:") and "gemini" not in reason


@pytest.mark.unit
@pytest.mark.usefixtures("gemini_off")
def test_chat_health_uses_the_same_switch() -> None:
    assert not is_provider_healthy("gemini")
    assert not is_provider_healthy("gemini_cli")


# ── registry probes (quick dispatch, code requests, CI remediation) ──────


@pytest.mark.unit
@pytest.mark.usefixtures("gemini_off")
def test_registry_probe_reports_disabled_rows_unavailable() -> None:
    rows = provider_probe.probe_provider_availability(credential_probes={})
    assert rows["gemini_cli"]["authenticated"] is False
    assert "disabled on this node" in rows["gemini_cli"]["detail"]
    assert "disabled" not in rows["codex_cli"]["detail"]


@pytest.mark.unit
@pytest.mark.usefixtures("gemini_off")
def test_legacy_probe_reports_disabled_rows_unavailable() -> None:
    rows = providers.probe_provider_availability(env={})
    assert rows["gemini_cli"].available is False
    assert rows["gemini_cli"].status == "disabled"
    assert rows["codex_cli"].status != "disabled"
