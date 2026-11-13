"""Staff alerts when dispatch context is degraded by budget trimming (#4420)."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

import push
from fleet_events import FleetEvent, get_event_store

log = logging.getLogger("dashboard.staff.context_budget")

_ALERT_DEBOUNCE_SECONDS = 6 * 3600.0
_alert_lock = threading.Lock()
_last_alert_at: dict[str, float] = {}


def build_context_degraded_alert(
    *,
    role: str,
    repo: str,
    trimmed: tuple[str, ...] | list[str],
    machine: str = "",
) -> dict[str, Any]:
    label = f"{role}@{repo or 'no-repo'}"
    what = ", ".join(trimmed) if trimmed else "optional tier-1 context"
    return {
        "key": f"context-degraded:{machine}:{label}",
        "severity": "warning",
        "title": "Staff dispatch context degraded",
        "detail": f"{label}: optional context trimmed ({what}) to satisfy token caps; run proceeded.",
        "node": machine or None,
        "role": role,
        "repo": repo,
        "trimmed": list(trimmed),
    }


def emit_context_degraded_alert(alert: dict[str, Any], now: datetime | None = None) -> bool:
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

    store = get_event_store()
    ts = int(now_s * 1000)
    store.record(
        FleetEvent(
            ts=ts,
            severity="warning",
            kind="watchdog",
            title=str(alert.get("title") or "Staff dispatch context degraded"),
            detail=str(alert.get("detail") or ""),
            node=alert.get("node"),
        )
    )
    push.notify(
        "staff.escalation",
        str(alert.get("title") or "Staff dispatch context degraded"),
        str(alert.get("detail") or ""),
    )
    log.warning("staff context budget: %s", alert.get("detail"))
    return True


def reset_context_degraded_alert_debounce_for_tests() -> None:
    with _alert_lock:
        _last_alert_at.clear()
