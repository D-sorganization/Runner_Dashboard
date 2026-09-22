"""Scheduled-role liveness: "when did this role last succeed?" (issue #1209).

The previous sweep mechanism stopped on 2026-05-27 and nobody noticed for four
months because nothing watched last-success. For every dispatchable role with a
``schedule`` this module derives, from the node-local run store and the
scheduler state file, a status:

  ``ok``     the latest successful run is younger than 1.5 schedule intervals;
  ``late``   older than 1.5 intervals but younger than 3;
  ``dead``   older than 3 intervals — or the role has fired at least once and
             never succeeded (a still-active first attempt is ``late``);
  ``never``  no run exists and the scheduler has not fired the role.

``compute_liveness`` is pure (roles, store, scheduler state, now). A role
turning ``dead`` is recorded once per role per six hours as a ``staff_role_dead``
fleet event (``notify_dead``); the debounce lives in memory.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from fleet_events import FleetEvent, get_event_store
from staff.roles import RoleSpec
from staff.schedule import DEFAULT_TZ, next_fire, parse_cron
from staff.scheduler import state_path
from staff.store import ACTIVE_STATUSES, RunRecord

log = logging.getLogger("dashboard.staff.liveness")

LIVENESS_STATUSES = ("ok", "late", "dead", "never")
ALERT_STATUSES = ("late", "dead")
LATE_FACTOR = 1.5
DEAD_FACTOR = 3.0
DEAD_EVENT_DEBOUNCE_SECONDS = 6 * 3600
DEAD_EVENT_KIND = "staff_role_dead"


class RunLookup(Protocol):
    """The slice of ``RunStore`` liveness needs (LoD: nothing else is reached)."""

    def list_runs(
        self, limit: int = ..., role: str | None = ..., status: str | None = ..., since: str | None = ...
    ) -> list[RunRecord]: ...


@dataclass(frozen=True)
class RoleLiveness:
    """Flat liveness row for one scheduled role (one entry in ``board.liveness``)."""

    role: str
    schedule: str
    status: str
    last_success: str | None
    last_attempt: str | None
    last_fired: str | None
    next_fire: str | None
    expected_interval_seconds: int | None
    age_seconds: int | None

    def to_dict(self) -> dict[str, Any]:
        assert self.status in LIVENESS_STATUSES, self.status  # noqa: S101
        return asdict(self)


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def expected_interval_seconds(schedule: str, now: datetime, tz: str = DEFAULT_TZ) -> int | None:
    """Gap between the next two fires of ``schedule`` after ``now``; ``None`` for a bad cron."""
    try:
        spec = parse_cron(schedule)
        first = next_fire(spec, now, tz)
        second = next_fire(spec, first, tz)
    except ValueError:
        return None
    return int((second - first).total_seconds())


def _latest(store: RunLookup, role: str, status: str | None = None) -> RunRecord | None:
    runs = store.list_runs(limit=1, role=role, status=status)
    return runs[0] if runs else None


def _classify(age: float, interval: int | None) -> str:
    if interval is None or interval <= 0:
        return "ok"
    if age > DEAD_FACTOR * interval:
        return "dead"
    if age > LATE_FACTOR * interval:
        return "late"
    return "ok"


def role_liveness(
    role: RoleSpec, store: RunLookup, state: dict[str, Any], now: datetime, tz: str = DEFAULT_TZ
) -> RoleLiveness:
    """Liveness of one scheduled role at ``now``. Pre: ``role.schedule`` is set, ``now`` is aware."""
    assert role.schedule, f"{role.name} has no schedule"  # noqa: S101
    assert now.tzinfo is not None, "now must be timezone-aware"  # noqa: S101
    interval = expected_interval_seconds(role.schedule, now, tz)
    success = _latest(store, role.name, "succeeded")
    attempt = _latest(store, role.name)
    success_at = _parse_iso((success.ended_at or success.created_at) if success else None)
    attempt_at = _parse_iso(attempt.created_at if attempt else None)
    fired_at = _parse_iso(str(state.get("last_fired") or "") or None)
    age: int | None = None
    if success_at is not None:
        age = int((now - success_at).total_seconds())
        status = _classify(age, interval)
    elif fired_at is not None or attempt_at is not None:
        anchor = max(t for t in (fired_at, attempt_at) if t is not None)
        age = int((now - anchor).total_seconds())
        status = "late" if attempt is not None and attempt.status in ACTIVE_STATUSES else "dead"
    else:
        status = "never"
    upcoming: str | None = None
    if interval is not None:
        upcoming = next_fire(role.schedule, now, tz).isoformat()
    return RoleLiveness(
        role=role.name,
        schedule=role.schedule,
        status=status,
        last_success=success_at.isoformat() if success_at else None,
        last_attempt=attempt_at.isoformat() if attempt_at else None,
        last_fired=fired_at.isoformat() if fired_at else None,
        next_fire=upcoming,
        expected_interval_seconds=interval,
        age_seconds=age,
    )


def compute_liveness(
    roles: dict[str, RoleSpec],
    store: RunLookup,
    scheduler_state: dict[str, dict[str, Any]],
    now: datetime,
    tz: str = DEFAULT_TZ,
) -> list[dict[str, Any]]:
    """One row per dispatchable scheduled role, sorted by name. Pure: no I/O beyond ``store``."""
    rows = [
        role_liveness(role, store, scheduler_state.get(name, {}), now, tz).to_dict()
        for name, role in sorted(roles.items())
        if role.schedule and role.dispatchable
    ]
    assert all(r["status"] in LIVENESS_STATUSES for r in rows)  # noqa: S101
    return rows


def load_scheduler_state(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """The scheduler's per-role ``{cursor, last_fired, last_reason}`` file; ``{}`` when absent or bad."""
    target = path or state_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except (OSError, ValueError) as exc:
        log.warning("staff liveness: state file %s unreadable (%s)", target, exc)
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}


def alerts(rows: list[dict[str, Any]], machine: str | None = None) -> list[dict[str, Any]]:
    """The late/dead rows, optionally tagged with the machine they came from."""
    return [{**r, "machine": machine} if machine else dict(r) for r in rows if r.get("status") in ALERT_STATUSES]


_dead_notified: dict[str, float] = {}
_dead_lock = threading.Lock()


def notify_dead(rows: list[dict[str, Any]], machine: str, clock: float | None = None) -> list[str]:
    """Record one ``staff_role_dead`` fleet event per dead role per six hours. Returns the roles notified."""
    now = time.time() if clock is None else clock
    fired: list[str] = []
    with _dead_lock:
        for row in rows:
            if row.get("status") != "dead":
                continue
            role = str(row["role"])
            if now - _dead_notified.get(role, float("-inf")) < DEAD_EVENT_DEBOUNCE_SECONDS:
                continue
            _dead_notified[role] = now
            fired.append(role)
    for role in fired:
        row = next(r for r in rows if r["role"] == role)
        detail = f"last success {row.get('last_success') or 'never'}; schedule {row.get('schedule')}"
        log.warning("staff liveness: role %s is dead on %s (%s)", role, machine, detail)
        get_event_store().record(
            FleetEvent(
                ts=int(now * 1000),
                severity="warning",
                kind=DEAD_EVENT_KIND,
                title=f"Staff role {role} dead",
                detail=detail,
                node=machine,
            )  # fmt: skip
        )
    return fired


def reset_dead_notifications() -> None:
    with _dead_lock:
        _dead_notified.clear()
