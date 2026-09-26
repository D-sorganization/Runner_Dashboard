"""Per-role daily USD budgets and threshold alerts (issue #1196).

``BudgetGuard.can_run(role)`` compares today's spend for the role (from the
run store) against ``budget.usd_per_day``; ``budget.usd_per_run`` is the
reservation a new run needs to fit under the cap. Alerts fire once per
(role, threshold) at 75 %, 90 % and 100 % with a six-hour debounce held in
memory — the shape Maxwell_Daemon uses for its own spend alarms, kept small.

Alerts go to an injectable sink (default: the dashboard log). The fleet event
log's ``FleetEvent.kind`` enum has no staff kind, so it is not used here.

Subscription plans (#1588): a role also needs one provider whose plan windows are
under its ceiling (``budget.max_window_percent``, default 85 %). Dollars stay a
notional effort figure; the plan windows are the real budget.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from staff import quota
from staff.roles import RoleSpec
from staff.schedule import DEFAULT_TZ
from staff.store import RunStore

log = logging.getLogger("dashboard.staff.budget")

ALERT_THRESHOLDS = (0.75, 0.9, 1.0)
ALERT_DEBOUNCE = timedelta(hours=6)

AlertSink = Callable[[str, str, float], None]  # (role, message, fraction)
QuotaCheck = Callable[[str, float], tuple[bool, str]]  # (provider, ceiling) -> (ok, reason)


def _log_alert(role: str, message: str, fraction: float) -> None:
    level = logging.ERROR if fraction >= 1.0 else logging.WARNING
    log.log(level, "staff budget: %s — %s", role, message)


def day_start_iso(now: datetime, tz: str = DEFAULT_TZ) -> str:
    """UTC ISO-Z string for local midnight of ``now``'s day in ``tz`` (matches ``created_at``)."""
    zone = ZoneInfo(tz)
    local = now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")


class BudgetGuard:
    """Daily budget gate + debounced threshold alerts for staff roles."""

    def __init__(
        self,
        store: RunStore,
        alert_sink: AlertSink = _log_alert,
        clock: Callable[[], datetime] | None = None,
        tz: str = DEFAULT_TZ,
        role_budgets: dict[str, float] | None = None,
        quota_check: QuotaCheck | None = None,
    ) -> None:
        self._store = store
        self._sink = alert_sink
        self._clock = clock or (lambda: datetime.now(ZoneInfo(tz)))
        self._tz = tz
        self._last_alert: dict[tuple[str, float], datetime] = {}
        self._role_budgets: dict[str, float] = dict(role_budgets or {})
        self._quota_check: QuotaCheck = quota_check or (lambda provider, ceiling: quota.headroom(provider, ceiling))

    def set_role_budget(self, role: str, budget_usd: float) -> None:
        """Register or override a role's daily USD budget cap."""
        self._role_budgets[role] = budget_usd

    def spent_today(self, role: str) -> float:
        now = self._clock()
        return float(self._store.spend_by_role_since(day_start_iso(now, self._tz)).get(role, 0.0))

    def can_run(self, role: RoleSpec) -> tuple[bool, str]:
        """(ok, reason). Also emits threshold alerts as a side effect.

        Post: ok needs the USD cap (none when ``usd_per_day <= 0``) and at least one
        of the role's providers under its plan-window ceiling; a quota refusal
        starts with ``quota:``.
        """
        ok, reason = self._usd_ok(role)
        if not ok:
            return ok, reason
        ceiling = quota.ceiling_percent(role.budget_max_window_percent)
        checks = [self._quota_check(provider, ceiling) for provider in role.providers]
        if checks and not any(fits for fits, _ in checks):
            return False, "quota: " + "; ".join(why for _, why in checks)
        return True, reason

    def can_dispatch(self, role: RoleSpec | None, provider: str) -> tuple[bool, str]:
        """(ok, reason) for a manual dispatch to the already-chosen ``provider`` (#1588).

        Pre: ``provider`` is the planned provider. Post: checks the role's USD cap
        (no alerts) and that provider's plan windows only.
        """
        reason = "no role budget"
        if role is not None:
            ok, reason = self._usd_ok(role, alerts=False)
            if not ok:
                return ok, reason
        ceiling = quota.ceiling_percent(role.budget_max_window_percent if role else None)
        fits, why = self._quota_check(provider, ceiling)
        return (True, reason) if fits else (False, f"quota: {why}")

    def _usd_ok(self, role: RoleSpec, *, alerts: bool = True) -> tuple[bool, str]:
        if role.budget_usd_per_day <= 0:
            return True, "no daily budget"
        spent = self.spent_today(role.name)
        cap = role.budget_usd_per_day
        if alerts:
            self.check_alerts(role.name, spent, cap)
        if spent >= cap:
            return False, f"daily budget ${cap:.2f} reached (spent ${spent:.2f})"
        if role.budget_usd_per_run > 0 and spent + role.budget_usd_per_run > cap:
            return False, f"next run (${role.budget_usd_per_run:.2f}) would exceed daily budget ${cap:.2f}"
        return True, f"${spent:.2f} of ${cap:.2f} spent today"

    def can_chat(self, role: str, role_budget_usd: float | None = None) -> tuple[bool, str]:
        """Check if role has remaining daily USD budget for conversational chat turns."""
        cap = role_budget_usd
        if cap is None:
            cap = self._role_budgets.get(role)
        if cap is None:
            from staff.runner import get_runner  # noqa: PLC0415

            runner = get_runner()
            spec = runner.roles().get(role)
            cap = getattr(spec, "budget_usd_per_day", 0.0) if spec else 0.0

        if cap <= 0:
            return True, "no daily budget"

        spent = self.spent_today(role)
        self.check_alerts(role, spent, cap)
        if spent >= cap:
            return False, f"daily budget ${cap:.2f} reached (spent ${spent:.2f})"
        return True, f"${spent:.2f} of ${cap:.2f} spent today"

    def check_alerts(self, role: str, spent: float, cap: float) -> list[str]:
        """Emit one alert per crossed threshold, at most once per six hours each."""
        if cap <= 0:
            return []
        now = self._clock()
        fraction = spent / cap
        emitted: list[str] = []
        for threshold in ALERT_THRESHOLDS:
            if fraction < threshold:
                continue
            last = self._last_alert.get((role, threshold))
            if last is not None and now - last < ALERT_DEBOUNCE:
                continue
            message = f"{int(threshold * 100)}% of daily budget: ${spent:.2f} of ${cap:.2f}"
            self._last_alert[(role, threshold)] = now
            self._sink(role, message, fraction)
            emitted.append(message)
        return emitted


_GLOBAL_BUDGET_GUARD: BudgetGuard | None = None


def get_global_budget_guard() -> BudgetGuard:
    """Retrieve or lazily initialize the process-wide BudgetGuard instance."""
    global _GLOBAL_BUDGET_GUARD  # noqa: PLW0603
    if _GLOBAL_BUDGET_GUARD is None:
        from staff.runner import get_runner  # noqa: PLC0415

        _GLOBAL_BUDGET_GUARD = BudgetGuard(store=get_runner().store)
    return _GLOBAL_BUDGET_GUARD


def set_global_budget_guard(guard: BudgetGuard) -> None:
    """Explicitly inject a BudgetGuard instance (for tests)."""
    global _GLOBAL_BUDGET_GUARD  # noqa: PLW0603
    _GLOBAL_BUDGET_GUARD = guard


def reset_global_budget_guard() -> None:
    """Reset the global BudgetGuard instance (for tests)."""
    global _GLOBAL_BUDGET_GUARD  # noqa: PLW0603
    _GLOBAL_BUDGET_GUARD = None
