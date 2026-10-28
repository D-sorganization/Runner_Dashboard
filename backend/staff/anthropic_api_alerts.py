"""anthropic-api staff/summary fields, 80% budget alert and use-it-or-lose-it nudge
(issue #3123 slice 3).

Spend figures are pure reads off the shared ``RunStore``/``AnthropicApiBudgetGate``
(slice 1); this module adds no new storage. The alert and nudge are expressed as
*decisions* (pure functions over :class:`~staff.anthropic_api_budget.BudgetStatus` and the
clock) plus a thin delivery function that reuses ``staff.barb_outbox.OutboxStore``'s own
``(kind, thread_id, run_id, version)`` idempotency as the "send at most once per period"
dedupe (DRY, matching ``staff.run_notifier.OutboxSink``'s documented precedent) instead of
inventing a parallel dedupe store: the alert keys on the calendar month, the nudge on the
calendar day, so a repeated call within the same period is a no-op, not a second send.

Which live Barb thread id the fleet-wide alert/nudge actually targets, and on what
schedule :func:`enqueue_budget_alert`/:func:`enqueue_use_it_or_lose_it_nudge` are
triggered, is a slice 4 decision (alongside the provider-registry/Settings-UI wiring
slice 2 already deferred there) — this slice ships the decision logic, wired for real
into ``staff/summary`` (:func:`summary_fields`), and tests the delivery functions against
the real outbox interface.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from staff.anthropic_api_budget import PROVIDER_ID, AnthropicApiBudgetGate, BudgetStatus
from staff.barb_outbox import OutboxStore
from staff.budget import day_start_iso
from staff.schedule import DEFAULT_TZ
from staff.store import RunStore

ALERT_KIND = "anthropic_api_budget_alert"
NUDGE_KIND = "anthropic_api_credit_nudge"
ALERT_FRACTION = 0.8
NUDGE_WINDOW_DAYS = 5
NUDGE_UNSPENT_FRACTION = 0.30


def _local(now: datetime, tz: str) -> datetime:
    zone = ZoneInfo(tz)
    return now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)


def month_key(now: datetime, tz: str = DEFAULT_TZ) -> str:
    """``"YYYY-MM"`` for ``now``'s local calendar month in ``tz`` (the alert's dedupe period)."""
    return _local(now, tz).strftime("%Y-%m")


def day_key(now: datetime, tz: str = DEFAULT_TZ) -> str:
    """``"YYYY-MM-DD"`` for ``now``'s local calendar day in ``tz`` (the nudge's dedupe period)."""
    return _local(now, tz).strftime("%Y-%m-%d")


def days_left_in_month(now: datetime, tz: str = DEFAULT_TZ) -> int:
    """Whole days left in ``now``'s local month in ``tz`` (the last day of the month is 0)."""
    local = _local(now, tz)
    last_day = calendar.monthrange(local.year, local.month)[1]
    return last_day - local.day


def month_end_date(now: datetime, tz: str = DEFAULT_TZ) -> str:
    """``"YYYY-MM-DD"`` for the last calendar day of ``now``'s local month in ``tz``."""
    local = _local(now, tz)
    last_day = calendar.monthrange(local.year, local.month)[1]
    return local.replace(day=last_day).strftime("%Y-%m-%d")


def should_alert(status: BudgetStatus) -> bool:
    """True once month-to-date spend reaches >= 80% of the cap (#3123 req. 5).

    Post: a zero/negative budget never alerts (there is no cap to measure against).
    """
    if status.budget_month_usd <= 0:
        return False
    return status.spend_month_usd / status.budget_month_usd >= ALERT_FRACTION


def alert_message(status: BudgetStatus) -> str:
    pct = (status.spend_month_usd / status.budget_month_usd * 100) if status.budget_month_usd > 0 else 0.0
    return (
        f"Anthropic API spend at {pct:.0f}% of the ${status.budget_month_usd:.2f} monthly cap "
        f"(${status.spend_month_usd:.2f} spent, ${status.remaining_usd:.2f} remaining)."
    )


def use_it_or_lose_it_message(status: BudgetStatus, now: datetime, tz: str = DEFAULT_TZ) -> str | None:
    """Nudge text once inside the last :data:`NUDGE_WINDOW_DAYS` days of the month with more
    than :data:`NUDGE_UNSPENT_FRACTION` of the credit unspent (#3123 req. 6), else ``None``.

    Post: always reflects current truth (a pure projection of ``status``/``now``) — the "at
    most once per day" cadence from req. 6 applies to the Barb-brief delivery
    (:func:`enqueue_use_it_or_lose_it_nudge`'s dedupe), not this read-only projection.
    """
    if status.budget_month_usd <= 0:
        return None
    if days_left_in_month(now, tz) > NUDGE_WINDOW_DAYS:
        return None
    unspent_fraction = status.remaining_usd / status.budget_month_usd
    if unspent_fraction <= NUDGE_UNSPENT_FRACTION:
        return None
    return (
        f"${status.remaining_usd:.2f} of ${status.budget_month_usd:.2f} Claude API credit "
        f"unused; expires {month_end_date(now, tz)}."
    )


def summary_fields(
    store: RunStore,
    *,
    clock: Callable[[], datetime] | None = None,
    tz: str = DEFAULT_TZ,
    budget_usd: float | None = None,
) -> dict[str, float | str | None]:
    """anthropic-api spend/budget figures for ``staff/summary`` (#3123 req. 4).

    Pre: ``store`` is the same ``RunStore`` the rest of the staff runner writes to (same
    as :class:`~staff.anthropic_api_budget.AnthropicApiBudgetGate`). Post: four USD
    figures plus ``anthropic_api_credit_nudge`` (a human string or ``None``); never raises.
    """
    now_fn = clock or (lambda: datetime.now(ZoneInfo(tz)))
    gate = AnthropicApiBudgetGate(store=store, clock=now_fn, tz=tz, budget_usd=budget_usd)
    status = gate.status()
    now = now_fn()
    today_spend = float(store.spend_since(day_start_iso(now, tz)).get(PROVIDER_ID, 0.0))
    return {
        "anthropic_api_spend_today_usd": today_spend,
        "anthropic_api_spend_month_usd": status.spend_month_usd,
        "anthropic_api_budget_month_usd": status.budget_month_usd,
        "anthropic_api_remaining_usd": status.remaining_usd,
        "anthropic_api_credit_nudge": use_it_or_lose_it_message(status, now, tz),
    }


def enqueue_budget_alert(
    outbox: OutboxStore, thread_id: str, status: BudgetStatus, now: datetime, tz: str = DEFAULT_TZ
) -> str | None:
    """Stage the 80% alert in the durable Barb outbox, once per calendar month (#3123 req. 5).

    Pre: ``thread_id`` is the Barb thread this alert targets (which thread that is
    fleet-wide is a slice 4 decision — see module docstring). Post: ``None`` when
    ``status`` is not yet at the threshold; otherwise the outbox entry id, the same id on
    every call within the same local month for the same ``thread_id`` (dedupe).
    """
    if not should_alert(status):
        return None
    payload = {
        "message": alert_message(status),
        "spend_month_usd": status.spend_month_usd,
        "budget_month_usd": status.budget_month_usd,
    }
    return outbox.enqueue(ALERT_KIND, thread_id, month_key(now, tz), 1, payload, terminal=True)


def enqueue_use_it_or_lose_it_nudge(
    outbox: OutboxStore, thread_id: str, status: BudgetStatus, now: datetime, tz: str = DEFAULT_TZ
) -> str | None:
    """Stage the use-it-or-lose-it nudge in the durable Barb outbox, once per calendar day
    (#3123 req. 6). Same dedupe approach as :func:`enqueue_budget_alert`, keyed by day.
    """
    message = use_it_or_lose_it_message(status, now, tz)
    if message is None:
        return None
    payload = {
        "message": message,
        "remaining_usd": status.remaining_usd,
        "budget_month_usd": status.budget_month_usd,
    }
    return outbox.enqueue(NUDGE_KIND, thread_id, day_key(now, tz), 1, payload, terminal=True)
