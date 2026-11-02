"""Consecutive missed scheduled-run tracking and alerts (#3822)."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("dashboard.staff.scheduler_missed_runs")

MISSED_ALERT_THRESHOLD = 2
_ALERT_DEBOUNCE_SECONDS = 300.0
_last_alert_at: dict[str, float] = {}
_alert_lock = threading.Lock()


def record_missed(entry: dict[str, Any]) -> int:
    """Increment the consecutive miss counter for one scheduler state entry."""
    count = int(entry.get("consecutive_missed_runs") or 0) + 1
    entry["consecutive_missed_runs"] = count
    return count


def record_success(entry: dict[str, Any]) -> None:
    entry["consecutive_missed_runs"] = 0


def should_alert(count: int) -> bool:
    return count >= MISSED_ALERT_THRESHOLD


def note_scheduled_miss(
    entry: dict[str, Any],
    *,
    role: str,
    job: str | None,
    slot: str,
    machine: str,
) -> int:
    count = record_missed(entry)
    if should_alert(count):
        emit_missed_run_alert(build_alert(role, job=job, slot=slot, count=count, machine=machine))
    return count


def build_alert(role: str, *, job: str | None, slot: str, count: int, machine: str) -> dict[str, Any]:
    label = f"{role}::{job}" if job else role
    return {
        "key": f"missed-scheduled-runs:{machine}:{label}",
        "severity": "warning",
        "title": f"Staff role missed {count} consecutive scheduled runs",
        "detail": f"{label} missed scheduled slot {slot} ({count} consecutive misses on {machine}).",
        "node": machine,
        "role": role,
        "job": job,
        "slot": slot,
        "count": count,
    }


def emit_missed_run_alert(alert: dict[str, Any], now: datetime | None = None) -> bool:
    key = str(alert.get("key") or "")
    if not key:
        return False
    cur = now or datetime.now(UTC)
    now_s = cur.timestamp()
    with _alert_lock:
        last = _last_alert_at.get(key, float("-inf"))
        if now_s - last < _ALERT_DEBOUNCE_SECONDS:
            return False
        _last_alert_at[key] = now_s
    import push  # noqa: PLC0415
    from fleet_events import FleetEvent, get_event_store  # noqa: PLC0415

    store = get_event_store()
    ts = int(now_s * 1000)
    store.record(
        FleetEvent(
            ts=ts,
            severity="warning",
            kind="watchdog",
            title=str(alert.get("title") or "Missed scheduled staff runs"),
            detail=str(alert.get("detail") or ""),
            node=alert.get("node"),
        )
    )
    push.notify(
        "staff.escalation",
        str(alert.get("title") or "Missed scheduled staff runs"),
        str(alert.get("detail") or ""),
    )
    log.warning("staff scheduler missed-run alert: %s", alert.get("detail"))
    return True


def reset_missed_run_alert_debounce_for_tests() -> None:
    with _alert_lock:
        _last_alert_at.clear()
