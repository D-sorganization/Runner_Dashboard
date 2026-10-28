"""Anthropic API provider: monthly spend ledger and hard budget gate (issue #3123 slice 1).

Ships inert: no key exists yet, so these tests only exercise the price table,
the month boundary math and the fail-closed budget gate against a real
``RunStore`` — no network call, no adapter (slice 2).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from staff.anthropic_api_budget import (
    PROVIDER_ID,
    AnthropicApiBudgetGate,
    estimate_call_cost_usd,
    month_start_iso,
    monthly_budget_usd,
)
from staff.store import RunRecord, RunStore

pytestmark = pytest.mark.unit


def _store(tmp_path: Path) -> RunStore:
    return RunStore(path=tmp_path / "runs.sqlite3")


def _spend(store: RunStore, *, cost_usd: float, created_at: str, provider: str = PROVIDER_ID) -> None:
    store.create_run(
        RunRecord(
            id=f"run-{provider}-{created_at}-{cost_usd}",
            role="r",
            provider=provider,
            model="claude-sonnet-5",
            machine="m",
            repo="repo",
            target_kind="prompt",
            target_ref="",
            prompt="",
            status="succeeded",
            created_at=created_at,
            cost_usd=cost_usd,
        )
    )


# ── monthly_budget_usd ────────────────────────────────────────────────────


def test_monthly_budget_defaults_to_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_MONTHLY_BUDGET_USD", raising=False)
    assert monthly_budget_usd() == 200.0


def test_monthly_budget_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_MONTHLY_BUDGET_USD", "50")
    assert monthly_budget_usd() == 50.0


@pytest.mark.parametrize("bad", ["nonsense", "0", "-10"])
def test_monthly_budget_invalid_override_falls_back(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("ANTHROPIC_API_MONTHLY_BUDGET_USD", bad)
    assert monthly_budget_usd() == 200.0


# ── month_start_iso ───────────────────────────────────────────────────────


def test_month_start_iso_is_first_of_month_midnight_pt() -> None:
    now = datetime(2026, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert month_start_iso(now) == "2026-07-01T07:00:00Z"


def test_month_start_iso_handles_local_month_still_prior_to_utc_date() -> None:
    # 2026-07-31 23:30 PDT is already 2026-08-01 in UTC, but still July in LA.
    now = datetime(2026, 7, 31, 23, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert month_start_iso(now) == "2026-07-01T07:00:00Z"


def test_month_start_iso_pst_offset_in_january() -> None:
    now = datetime(2026, 1, 15, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert month_start_iso(now) == "2026-01-01T08:00:00Z"


# ── AnthropicApiBudgetGate ─────────────────────────────────────────────────


def test_spend_month_to_date_sums_only_anthropic_api_this_month(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _spend(store, cost_usd=10.0, created_at="2026-10-15T00:00:00Z")
    _spend(store, cost_usd=5.0, created_at="2026-10-20T00:00:00Z")
    _spend(store, cost_usd=999.0, created_at="2026-09-30T23:00:00Z")  # last month
    _spend(store, cost_usd=999.0, created_at="2026-10-15T00:00:00Z", provider="codex")  # other provider

    gate = AnthropicApiBudgetGate(store=store, clock=lambda: datetime(2026, 10, 27, tzinfo=ZoneInfo("UTC")))
    assert gate.spend_month_to_date() == 15.0


def test_can_spend_allows_under_cap(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _spend(store, cost_usd=100.0, created_at="2026-10-01T10:00:00Z")
    gate = AnthropicApiBudgetGate(
        store=store, clock=lambda: datetime(2026, 10, 15, tzinfo=ZoneInfo("UTC")), budget_usd=200.0
    )
    ok, reason = gate.can_spend(50.0)
    assert ok
    assert "100.00" in reason


def test_can_spend_fails_closed_over_cap(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _spend(store, cost_usd=180.0, created_at="2026-10-01T10:00:00Z")
    gate = AnthropicApiBudgetGate(
        store=store, clock=lambda: datetime(2026, 10, 15, tzinfo=ZoneInfo("UTC")), budget_usd=200.0
    )
    ok, reason = gate.can_spend(25.0)  # 180 + 25 = 205 > 200
    assert not ok
    assert reason.startswith("budget_exhausted")


def test_can_spend_allows_exact_cap(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _spend(store, cost_usd=150.0, created_at="2026-10-01T10:00:00Z")
    gate = AnthropicApiBudgetGate(
        store=store, clock=lambda: datetime(2026, 10, 15, tzinfo=ZoneInfo("UTC")), budget_usd=200.0
    )
    ok, _ = gate.can_spend(50.0)  # exactly at cap
    assert ok


def test_can_spend_resets_on_new_month(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _spend(store, cost_usd=200.0, created_at="2026-09-15T00:00:00Z")  # maxed last month
    gate = AnthropicApiBudgetGate(
        store=store, clock=lambda: datetime(2026, 10, 2, 12, tzinfo=ZoneInfo("UTC")), budget_usd=200.0
    )
    ok, _ = gate.can_spend(50.0)
    assert ok


def test_can_spend_negative_estimate_clamps_to_zero(tmp_path: Path) -> None:
    store = _store(tmp_path)
    gate = AnthropicApiBudgetGate(
        store=store, clock=lambda: datetime(2026, 10, 15, tzinfo=ZoneInfo("UTC")), budget_usd=200.0
    )
    ok, _ = gate.can_spend(-5.0)
    assert ok


def test_status_reports_remaining(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _spend(store, cost_usd=150.0, created_at="2026-10-01T10:00:00Z")
    gate = AnthropicApiBudgetGate(
        store=store, clock=lambda: datetime(2026, 10, 15, tzinfo=ZoneInfo("UTC")), budget_usd=200.0
    )
    status = gate.status()
    assert status.spend_month_usd == 150.0
    assert status.budget_month_usd == 200.0
    assert status.remaining_usd == 50.0


def test_status_remaining_never_negative_when_overspent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _spend(store, cost_usd=250.0, created_at="2026-10-01T10:00:00Z")
    gate = AnthropicApiBudgetGate(
        store=store, clock=lambda: datetime(2026, 10, 15, tzinfo=ZoneInfo("UTC")), budget_usd=200.0
    )
    assert gate.status().remaining_usd == 0.0


def test_budget_usd_falls_back_to_env_when_not_pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_MONTHLY_BUDGET_USD", "75")
    store = _store(tmp_path)
    gate = AnthropicApiBudgetGate(store=store)
    assert gate.budget_usd() == 75.0


# ── estimate_call_cost_usd (reuses the shared price table, DRY) ───────────


def test_estimate_call_cost_uses_shared_price_table() -> None:
    usd = estimate_call_cost_usd("claude-sonnet-5", 1_000_000, 100_000)
    assert usd == pytest.approx(3.0)  # 1M in @ $2/1M + 100k out @ $10/1M


def test_estimate_call_cost_unknown_model_is_zero() -> None:
    assert estimate_call_cost_usd("totally-unknown-model", 10, 10) == 0.0
