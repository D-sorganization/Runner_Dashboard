"""anthropic-api staff/summary fields, 80% alert and use-it-or-lose-it nudge (issue #3123 slice 3).

Ships inert: the alert/nudge functions exercise the real ``OutboxStore`` interface
(slice 1's gate plus the shared Barb outbox), but nothing here sends a live request or
decides a production thread id — that is a slice 4 wiring decision (see module docstring).
"""

from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from staff.anthropic_api_alerts import (
    ALERT_KIND,
    NUDGE_KIND,
    alert_message,
    day_key,
    days_left_in_month,
    enqueue_budget_alert,
    enqueue_use_it_or_lose_it_nudge,
    month_end_date,
    month_key,
    should_alert,
    summary_fields,
    use_it_or_lose_it_message,
)
from staff.anthropic_api_budget import PROVIDER_ID, BudgetStatus
from staff.barb_outbox import OutboxStore
from staff.conversations import ConversationStore
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


def _outbox(tmp_path: Path) -> OutboxStore:
    conv = ConversationStore(tmp_path / "node.sqlite3")
    return conv.barb_outbox(max_entries=50, max_bytes=1_000_000, max_entry_bytes=10_000, rng=random.Random(1))


def _status(spend: float, budget: float) -> BudgetStatus:
    return BudgetStatus(spend_month_usd=spend, budget_month_usd=budget)


# ── month_key / day_key / days_left_in_month / month_end_date ────────────────


def test_month_key_is_year_month_in_la_time() -> None:
    now = datetime(2026, 10, 27, 23, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert month_key(now) == "2026-10"


def test_month_key_crosses_month_boundary_in_utc_but_not_la() -> None:
    # 2026-10-31 23:30 PDT is already 2026-11-01 in UTC, but still October in LA.
    now = datetime(2026, 10, 31, 23, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert month_key(now) == "2026-10"


def test_day_key_is_year_month_day_in_la_time() -> None:
    now = datetime(2026, 10, 27, 23, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert day_key(now) == "2026-10-27"


def test_days_left_in_month_at_month_start() -> None:
    now = datetime(2026, 10, 1, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert days_left_in_month(now) == 30  # October has 31 days


def test_days_left_in_month_on_last_day() -> None:
    now = datetime(2026, 10, 31, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert days_left_in_month(now) == 0


def test_month_end_date_is_last_calendar_day() -> None:
    now = datetime(2026, 2, 5, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert month_end_date(now) == "2026-02-28"


# ── should_alert / alert_message ──────────────────────────────────────────────


def test_should_alert_false_under_80_percent() -> None:
    assert should_alert(_status(spend=79.99, budget=200.0)) is False


def test_should_alert_true_at_exactly_80_percent() -> None:
    assert should_alert(_status(spend=160.0, budget=200.0)) is True


def test_should_alert_true_over_80_percent() -> None:
    assert should_alert(_status(spend=190.0, budget=200.0)) is True


def test_should_alert_false_when_budget_is_zero() -> None:
    assert should_alert(_status(spend=0.0, budget=0.0)) is False


def test_alert_message_mentions_percent_and_amounts() -> None:
    message = alert_message(_status(spend=160.0, budget=200.0))
    assert "80%" in message
    assert "160.00" in message
    assert "40.00" in message  # remaining


# ── use_it_or_lose_it_message ──────────────────────────────────────────────────


def test_nudge_absent_outside_last_5_days() -> None:
    now = datetime(2026, 10, 20, tzinfo=ZoneInfo("America/Los_Angeles"))  # 11 days left
    assert use_it_or_lose_it_message(_status(spend=0.0, budget=200.0), now) is None


def test_nudge_absent_when_unspent_fraction_at_or_below_30_percent() -> None:
    now = datetime(2026, 10, 28, tzinfo=ZoneInfo("America/Los_Angeles"))  # 3 days left
    # remaining 60/200 = 30% exactly -> not "more than 30%"
    assert use_it_or_lose_it_message(_status(spend=140.0, budget=200.0), now) is None


def test_nudge_present_in_last_5_days_with_over_30_percent_unspent() -> None:
    now = datetime(2026, 10, 28, tzinfo=ZoneInfo("America/Los_Angeles"))  # 3 days left
    message = use_it_or_lose_it_message(_status(spend=100.0, budget=200.0), now)
    assert message is not None
    assert "100.00" in message
    assert "200.00" in message
    assert "2026-10-31" in message


def test_nudge_absent_when_budget_is_zero() -> None:
    now = datetime(2026, 10, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert use_it_or_lose_it_message(_status(spend=0.0, budget=0.0), now) is None


# ── summary_fields ─────────────────────────────────────────────────────────────


def test_summary_fields_reports_today_month_budget_and_remaining(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _spend(store, cost_usd=5.0, created_at="2026-10-27T12:00:00Z")  # today
    _spend(store, cost_usd=20.0, created_at="2026-10-01T10:00:00Z")  # earlier this month
    fields = summary_fields(store, clock=lambda: datetime(2026, 10, 27, 15, tzinfo=ZoneInfo("UTC")), budget_usd=200.0)
    assert fields["anthropic_api_spend_today_usd"] == 5.0
    assert fields["anthropic_api_spend_month_usd"] == 25.0
    assert fields["anthropic_api_budget_month_usd"] == 200.0
    assert fields["anthropic_api_remaining_usd"] == 175.0


def test_summary_fields_nudge_is_none_well_before_month_end(tmp_path: Path) -> None:
    store = _store(tmp_path)
    fields = summary_fields(store, clock=lambda: datetime(2026, 10, 10, tzinfo=ZoneInfo("UTC")), budget_usd=200.0)
    assert fields["anthropic_api_credit_nudge"] is None


def test_summary_fields_never_raises_with_empty_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    fields = summary_fields(store, clock=lambda: datetime(2026, 10, 27, tzinfo=ZoneInfo("UTC")))
    assert fields["anthropic_api_spend_today_usd"] == 0.0


# ── enqueue_budget_alert / enqueue_use_it_or_lose_it_nudge (outbox dedupe) ────


def test_enqueue_budget_alert_stages_once_when_due(tmp_path: Path) -> None:
    outbox = _outbox(tmp_path)
    now = datetime(2026, 10, 15, tzinfo=ZoneInfo("America/Los_Angeles"))
    outbox_id = enqueue_budget_alert(outbox, "thr-budget", _status(spend=170.0, budget=200.0), now)
    assert outbox_id is not None


def test_enqueue_budget_alert_is_noop_when_not_due(tmp_path: Path) -> None:
    outbox = _outbox(tmp_path)
    now = datetime(2026, 10, 15, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert enqueue_budget_alert(outbox, "thr-budget", _status(spend=10.0, budget=200.0), now) is None


def test_enqueue_budget_alert_dedupes_within_same_month(tmp_path: Path) -> None:
    outbox = _outbox(tmp_path)
    now = datetime(2026, 10, 15, tzinfo=ZoneInfo("America/Los_Angeles"))
    status = _status(spend=170.0, budget=200.0)
    first = enqueue_budget_alert(outbox, "thr-budget", status, now)
    later = datetime(2026, 10, 29, tzinfo=ZoneInfo("America/Los_Angeles"))
    second = enqueue_budget_alert(outbox, "thr-budget", status, later)
    assert first == second  # same (kind, thread, month) key -> same outbox row, not a new send


def test_enqueue_budget_alert_fires_again_next_month(tmp_path: Path) -> None:
    outbox = _outbox(tmp_path)
    status = _status(spend=170.0, budget=200.0)
    first = enqueue_budget_alert(
        outbox, "thr-budget", status, datetime(2026, 10, 15, tzinfo=ZoneInfo("America/Los_Angeles"))
    )
    second = enqueue_budget_alert(
        outbox, "thr-budget", status, datetime(2026, 11, 2, tzinfo=ZoneInfo("America/Los_Angeles"))
    )
    assert first != second


def test_enqueue_nudge_stages_once_when_due(tmp_path: Path) -> None:
    outbox = _outbox(tmp_path)
    now = datetime(2026, 10, 28, tzinfo=ZoneInfo("America/Los_Angeles"))
    outbox_id = enqueue_use_it_or_lose_it_nudge(outbox, "thr-nudge", _status(spend=100.0, budget=200.0), now)
    assert outbox_id is not None


def test_enqueue_nudge_is_noop_when_not_due(tmp_path: Path) -> None:
    outbox = _outbox(tmp_path)
    now = datetime(2026, 10, 10, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert enqueue_use_it_or_lose_it_nudge(outbox, "thr-nudge", _status(spend=100.0, budget=200.0), now) is None


def test_enqueue_nudge_dedupes_within_same_day(tmp_path: Path) -> None:
    outbox = _outbox(tmp_path)
    status = _status(spend=100.0, budget=200.0)
    now = datetime(2026, 10, 28, 9, tzinfo=ZoneInfo("America/Los_Angeles"))
    later_same_day = datetime(2026, 10, 28, 18, tzinfo=ZoneInfo("America/Los_Angeles"))
    first = enqueue_use_it_or_lose_it_nudge(outbox, "thr-nudge", status, now)
    second = enqueue_use_it_or_lose_it_nudge(outbox, "thr-nudge", status, later_same_day)
    assert first == second


def test_enqueue_alert_and_nudge_use_distinct_kinds(tmp_path: Path) -> None:
    # Alert needs >=80% spent; nudge needs >30% unspent (<70% spent) — the two
    # conditions never hold for the same status, so each gets its own scenario.
    outbox = _outbox(tmp_path)
    now = datetime(2026, 10, 28, tzinfo=ZoneInfo("America/Los_Angeles"))
    enqueue_budget_alert(outbox, "thr", _status(spend=170.0, budget=200.0), now)
    enqueue_use_it_or_lose_it_nudge(outbox, "thr", _status(spend=100.0, budget=200.0), now)
    alert_entries = outbox.pending_for("thr", month_key(now))
    nudge_entries = outbox.pending_for("thr", day_key(now))
    assert [e.kind for e in alert_entries] == [ALERT_KIND]
    assert [e.kind for e in nudge_entries] == [NUDGE_KIND]
