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
    """Alert once per streak: on the miss that reaches the threshold, not on later ones."""
    return count == MISSED_ALERT_THRESHOLD


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


def record_scheduled_skip(
    entry: dict[str, Any],
    *,
    role: str,
    job: str | None,
    slot: str,
    machine: str,
    reason: str,
) -> None:
    entry["last_reason"] = f"skipped {slot}: {reason}"
    note_scheduled_miss(entry, role=role, job=job, slot=slot, machine=machine)


def record_scheduled_fire(
    entry: dict[str, Any], *, slot: str, run_id: str, fired_at: str, role: str, job: str | None
) -> None:
    """Note a fired slot; its run's outcome decides the streak in ``reconcile_scheduled_runs``."""
    entry["last_fired"] = fired_at
    entry["last_reason"] = f"fired {slot} as {run_id}"
    entry["pending_run"] = {"run_id": run_id, "slot": slot, "role": role, "job": job}


_LIVE_STATUSES = ("queued", "preparing", "running", "needs_input")


def reconcile_scheduled_runs(state: dict[str, dict[str, Any]], *, store: Any, machine: str) -> bool:
    """Settle fired slots whose run went terminal. Returns True when ``state`` changed.

    A ``succeeded`` run resets the streak; a failed or cancelled run, or a succeeded run whose
    post-run verification failed, is a missed slot. A run that is still live waits; one the
    store no longer knows is a miss.
    """
    changed = False
    for entry in state.values():
        pending = entry.get("pending_run")
        if not isinstance(pending, dict):
            continue
        rec = store.get_run(str(pending.get("run_id") or ""))
        if rec is not None and rec.status in _LIVE_STATUSES:
            continue
        entry.pop("pending_run")
        changed = True
        if rec is not None and rec.status == "succeeded" and rec.verification != "failed":
            record_success(entry)
            continue
        outcome = rec.status if rec is not None else "missing"
        entry["last_reason"] = f"run {pending.get('run_id')} for {pending.get('slot')} ended {outcome}"
        note_scheduled_miss(
            entry,
            role=str(pending.get("role") or ""),
            job=pending.get("job"),
            slot=str(pending.get("slot") or ""),
            machine=machine,
        )
    return changed


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
