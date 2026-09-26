"""Budgets as a share of each plan's windows (#1588).

A role may run while at least one of its subscription providers is under the
window ceiling (``budget.max_window_percent``, else ``STAFF_QUOTA_CEILING_PERCENT``,
else 85 %). Manual dispatch is gated the same way unless ``ignore_budget`` is set,
and that override is audited.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from staff import budget as budget_mod
from staff import quota, usage
from staff import runner as runner_mod
from staff.audit import get_audit_store, reset_audit_store
from staff.rate_limit import reset_rate_limiter
from staff.roles import RoleSpec, parse_role

from tests.api.test_staff_fleet import client, staff  # noqa: F401  (shared /run fixtures)

_XHR = {"X-Requested-With": "XMLHttpRequest"}


def _snap(account: str, pct: float, *, limited: bool = False) -> quota.QuotaSnapshot:
    now = datetime.now(UTC)
    return quota.QuotaSnapshot(
        account=account,
        observed_at=now,
        source="test",
        windows=(quota.QuotaWindow("five_hour", pct, now + timedelta(hours=2)),),
        limited_until=now + timedelta(hours=1) if limited else None,
    )


@pytest.fixture
def quota_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> quota.QuotaStore:
    monkeypatch.setenv("STAFF_QUOTA_STATE", str(tmp_path / "quota.json"))
    monkeypatch.setenv("STAFF_CODEX_SESSION_DIRS", str(tmp_path / "no-sessions"))
    monkeypatch.delenv("STAFF_QUOTA_CEILING_PERCENT", raising=False)
    return quota.default_store()


@pytest.fixture(autouse=True)
def _isolated_policy_state() -> Any:
    reset_audit_store()
    reset_rate_limiter()
    yield
    reset_audit_store()
    reset_rate_limiter()


# ── role field and ceiling ───────────────────────────────────────────────


@pytest.mark.unit
def test_role_reads_max_window_percent() -> None:
    role = parse_role({"name": "r", "budget": {"max_window_percent": 70}})
    assert role.budget_max_window_percent == 70.0
    assert role.to_dict()["budget"]["max_window_percent"] == 70.0
    for bad in (0, 150, "lots", None):
        assert parse_role({"name": "r", "budget": {"max_window_percent": bad}}).budget_max_window_percent is None


@pytest.mark.unit
def test_ceiling_prefers_role_then_env_then_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAFF_QUOTA_CEILING_PERCENT", raising=False)
    assert quota.ceiling_percent(None) == 85.0
    monkeypatch.setenv("STAFF_QUOTA_CEILING_PERCENT", "70")
    assert quota.ceiling_percent(None) == 70.0
    assert quota.ceiling_percent(60.0) == 60.0
    monkeypatch.setenv("STAFF_QUOTA_CEILING_PERCENT", "nonsense")
    assert quota.ceiling_percent(None) == 85.0


# ── headroom ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_headroom_blocks_at_ceiling_and_on_hard_limit(quota_store: quota.QuotaStore) -> None:
    assert quota.headroom("claude", 85.0) == (True, "claude: no quota data")
    quota_store.record(_snap("claude", 90.0))
    ok, reason = quota.headroom("claude", 85.0)
    assert not ok and "five_hour" in reason and "90" in reason
    assert quota.headroom("claude", 95.0)[0]
    quota_store.record(_snap("codex", 10.0, limited=True))
    ok, reason = quota.headroom("codex", 85.0)
    assert not ok and "limit" in reason
    assert quota.headroom("ollama", 85.0)[0]  # local providers have no plan


# ── BudgetGuard ──────────────────────────────────────────────────────────


def _guard(staff: runner_mod.StaffRunner, over: set[str]) -> budget_mod.BudgetGuard:  # noqa: F811
    return budget_mod.BudgetGuard(
        staff.store, quota_check=lambda p, c: (p not in over, f"{p}: {'over' if p in over else 'under'} {c:g}%")
    )


@pytest.mark.unit
def test_can_run_needs_one_provider_under_the_ceiling(staff: runner_mod.StaffRunner) -> None:  # noqa: F811
    role = RoleSpec(name="r", title="R", providers=("claude", "codex"))
    assert _guard(staff, {"claude"}).can_run(role)[0]
    ok, reason = _guard(staff, {"claude", "codex"}).can_run(role)
    assert not ok and reason.startswith("quota:") and "claude" in reason and "codex" in reason


@pytest.mark.unit
def test_can_dispatch_checks_the_chosen_provider(staff: runner_mod.StaffRunner) -> None:  # noqa: F811
    role = RoleSpec(name="r", title="R", providers=("claude", "codex"), budget_max_window_percent=50.0)
    guard = _guard(staff, {"claude"})
    ok, reason = guard.can_dispatch(role, "claude")
    assert not ok and reason == "quota: claude: over 50%"
    assert guard.can_dispatch(role, "codex")[0]
    assert guard.can_dispatch(None, "codex")[0]


# ── runner provider choice ───────────────────────────────────────────────


@pytest.mark.unit
def test_runner_skips_providers_over_quota(
    staff: runner_mod.StaffRunner,  # noqa: F811
    quota_store: quota.QuotaStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for pid in ("claude", "codex"):
        monkeypatch.setattr(type(staff._adapters[pid]), "installed", lambda _self: True)  # noqa: SLF001
    assert staff._first_available(("claude", "codex")) == "claude"  # noqa: SLF001
    quota_store.record(_snap("claude", 95.0))
    assert staff._first_available(("claude", "codex")) == "codex"  # noqa: SLF001
    quota_store.record(_snap("codex", 99.0))
    assert staff._first_available(("claude", "codex")) == "claude"  # all over: keep the order


# ── manual dispatch gate ─────────────────────────────────────────────────


@pytest.mark.unit
def test_dispatch_refuses_a_provider_over_quota_unless_overridden(
    client: TestClient,  # noqa: F811
    staff: runner_mod.StaffRunner,  # noqa: F811
    quota_store: quota.QuotaStore,
) -> None:
    quota_store.record(_snap("claude", 97.0))
    resp = client.post("/api/staff/ad-hoc/run", json={"prompt": "hi", "provider": "claude"}, headers=_XHR)
    assert resp.status_code == 429, resp.text
    assert "quota" in str(resp.json()["detail"])
    assert staff.store.list_runs(limit=5) == []

    resp = client.post(
        "/api/staff/ad-hoc/run", json={"prompt": "hi", "provider": "claude", "ignore_budget": True}, headers=_XHR
    )
    assert resp.status_code == 200, resp.text
    [entry] = get_audit_store().list_entries(action="dispatch")
    assert entry.to_dict()["detail"]["ignore_budget"] is True
    assert "quota" in entry.to_dict()["detail"]["budget_reason"]


# ── one day boundary, notional dollars ───────────────────────────────────


@pytest.mark.unit
def test_usage_day_matches_the_budget_day() -> None:
    assert usage.today_iso() == budget_mod.day_start_iso(datetime.now(UTC))


@pytest.mark.unit
def test_usage_summary_marks_dollars_notional(staff: runner_mod.StaffRunner) -> None:  # noqa: F811
    body = usage.summary(staff.store, group="provider")
    assert body["totals"]["cost_basis"] == "notional"


@pytest.mark.unit
def test_gemini_signs_in_with_google_not_an_api_key() -> None:
    from agent_remediation.provider_registry import PROVIDER_REGISTRY  # noqa: PLC0415

    gemini = next(e for e in PROVIDER_REGISTRY if e.dashboard_id == "gemini_cli")
    assert gemini.auth_mode == "local"
    assert "GOOGLE_API_KEY" not in gemini.required_env


@pytest.mark.unit
def test_gemini_probe_accepts_oauth_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import credentials  # noqa: PLC0415

    monkeypatch.setattr(credentials.shutil, "which", lambda cmd: "/usr/bin/gemini" if cmd == "gemini" else None)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path))
    assert credentials._probe_gemini_cli()["usable"] is False  # noqa: SLF001
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".gemini" / "oauth_creds.json").write_text("{}", encoding="utf-8")
    probe = credentials._probe_gemini_cli()  # noqa: SLF001
    assert probe["usable"] is True and probe["key_status"] == "oauth"


@pytest.mark.unit
def test_validator_checks_max_window_percent() -> None:
    from staff.validator import _validate_dicts  # noqa: PLC0415

    def problems(value: object) -> list[str]:
        data = {"scope": {}, "budget": {"usd_per_run": 0, "usd_per_day": 0, "max_window_percent": value}}
        return [p for p in _validate_dicts(data) if "max_window_percent" in p]

    assert problems(80) == []
    assert problems(0) and problems(101) and problems("lots")
