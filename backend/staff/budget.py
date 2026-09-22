"""Per-role daily USD budgets and threshold alerts (issue #1196).

``BudgetGuard.can_run(role)`` compares today's spend for the role (from the
run store) against ``budget.usd_per_day``; ``budget.usd_per_run`` is the
reservation a new run needs to fit under the cap. Alerts fire once per
(role, threshold) at 75 %, 90 % and 100 % with a six-hour debounce held in
memory — the shape Maxwell_Daemon uses for its own spend alarms, kept small.

Alerts go to an injectable sink (default: the dashboard log). The fleet event
log's ``FleetEvent.kind`` enum has no staff kind, so it is not used here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from staff.roles import RoleSpec
from staff.schedule import DEFAULT_TZ
from staff.store import RunStore

log = logging.getLogger("dashboard.staff.budget")

ALERT_THRESHOLDS = (0.75, 0.9, 1.0)
ALERT_DEBOUNCE = timedelta(hours=6)

AlertSink = Callable[[str, str, float], None]  # (role, message, fraction)


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
    ) -> None:
        self._store = store
        self._sink = alert_sink
        self._clock = clock or (lambda: datetime.now(ZoneInfo(tz)))
        self._tz = tz
        self._last_alert: dict[tuple[str, float], datetime] = {}

    def spent_today(self, role: str) -> float:
        now = self._clock()
        return float(self._store.spend_by_role_since(day_start_iso(now, self._tz)).get(role, 0.0))

    def can_run(self, role: RoleSpec) -> tuple[bool, str]:
        """(ok, reason). Also emits threshold alerts as a side effect.

        Post: ok is True whenever the role has no daily budget (``usd_per_day <= 0``).
        """
        if role.budget_usd_per_day <= 0:
            return True, "no daily budget"
        spent = self.spent_today(role.name)
        cap = role.budget_usd_per_day
        self.check_alerts(role.name, spent, cap)
        if spent >= cap:
            return False, f"daily budget ${cap:.2f} reached (spent ${spent:.2f})"
        if role.budget_usd_per_run > 0 and spent + role.budget_usd_per_run > cap:
            return False, f"next run (${role.budget_usd_per_run:.2f}) would exceed daily budget ${cap:.2f}"
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
