"""Anthropic API provider: monthly spend ledger and hard budget gate (issue #3123 slice 1).

Ships inert — no key exists yet; slice 2 adds the adapter that will create the
``anthropic-api`` run records this gate reads, keyed the same way every other
provider's spend already is (``RunStore.spend_since``, issue #1200/#1196). The
cap is the subscription's $200/month included credit (expires if unused, not
pay-per-use overflow) and resets on the 1st of each calendar month in
America/Los_Angeles. A call that would push month-to-date spend over the cap
is refused before it is sent: ``AnthropicApiBudgetGate.can_spend`` fails
closed and never raises.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from staff import pricing
from staff.schedule import DEFAULT_TZ
from staff.store import RunStore

PROVIDER_ID = "anthropic-api"
DEFAULT_MONTHLY_BUDGET_USD = 200.0
BUDGET_ENV_VAR = "ANTHROPIC_API_MONTHLY_BUDGET_USD"


def monthly_budget_usd() -> float:
    """Hard monthly USD cap, overridable via ``ANTHROPIC_API_MONTHLY_BUDGET_USD``.

    Post: always positive; a missing, non-numeric or non-positive override
    falls back to ``DEFAULT_MONTHLY_BUDGET_USD`` rather than disabling the cap.
    """
    raw = os.environ.get(BUDGET_ENV_VAR, "")
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return DEFAULT_MONTHLY_BUDGET_USD
        if value > 0:
            return value
    return DEFAULT_MONTHLY_BUDGET_USD


def month_start_iso(now: datetime, tz: str = DEFAULT_TZ) -> str:
    """UTC ISO-Z string for local midnight of the 1st of ``now``'s month in ``tz``."""
    zone = ZoneInfo(tz)
    local = now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class BudgetStatus:
    """Snapshot shape for ``staff/summary``-style spend reporting (slice 3)."""

    spend_month_usd: float
    budget_month_usd: float

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.budget_month_usd - self.spend_month_usd)


class AnthropicApiBudgetGate:
    """Hard monthly budget gate for the ``anthropic-api`` provider.

    Pre: ``store`` is the same ``RunStore`` the rest of the staff runner
    writes to, so spend recorded by any future caller (adapter, backfill) is
    visible here. Post: ``can_spend`` only answers whether the caller may
    proceed — it never sends a request itself.
    """

    def __init__(
        self,
        store: RunStore,
        clock: Callable[[], datetime] | None = None,
        tz: str = DEFAULT_TZ,
        budget_usd: float | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(ZoneInfo(tz)))
        self._tz = tz
        self._budget_usd = budget_usd

    def budget_usd(self) -> float:
        return self._budget_usd if self._budget_usd is not None else monthly_budget_usd()

    def spend_month_to_date(self) -> float:
        since = month_start_iso(self._clock(), self._tz)
        return float(self._store.spend_since(since).get(PROVIDER_ID, 0.0))

    def status(self) -> BudgetStatus:
        return BudgetStatus(spend_month_usd=self.spend_month_to_date(), budget_month_usd=self.budget_usd())

    def can_spend(self, estimated_cost_usd: float) -> tuple[bool, str]:
        """(ok, reason). Fails closed: a bad estimate never lets a call through.

        Pre: ``estimated_cost_usd`` from ``pricing.estimate_cost`` or similar;
        negative values clamp to 0. Post: refuses once month-to-date spend
        plus the estimate would exceed the cap.
        """
        estimate = max(0.0, float(estimated_cost_usd or 0.0))
        cap = self.budget_usd()
        spent = self.spend_month_to_date()
        if spent + estimate > cap:
            return False, f"budget_exhausted: ${spent:.2f} spent of ${cap:.2f} monthly cap"
        return True, f"${spent:.2f} of ${cap:.2f} spent this month"


def estimate_call_cost_usd(model: str | None, input_tokens: int, output_tokens: int) -> float:
    """USD estimate for one Messages API call, reusing the shared price table."""
    usd, _method = pricing.estimate_cost(PROVIDER_ID, model, input_tokens=input_tokens, output_tokens=output_tokens)
    return usd
