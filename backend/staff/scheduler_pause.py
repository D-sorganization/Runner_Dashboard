"""Scheduler pause TTL for intentional ``STAFF_SCHEDULER_ENABLED=0`` on the hub (#3821).

Worker nodes keep the scheduler disabled permanently; only ``MACHINE_ROLE=hub``
participates in pause expiry. A hub pause must carry an end time from
``STAFF_SCHEDULER_PAUSE_UNTIL`` or from ``STAFF_SCHEDULER_PAUSE_TTL_SECONDS``
(default 72h) recorded in ``staff_scheduler_pause.json`` on first observation.
When ``now`` passes ``pause_until``, callers emit a debounced fleet alert.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from dashboard_config import MACHINE_ROLE
from staff.store import _config_dir

log = logging.getLogger("dashboard.staff.scheduler_pause")

PAUSE_UNTIL_ENV = "STAFF_SCHEDULER_PAUSE_UNTIL"
PAUSE_TTL_ENV = "STAFF_SCHEDULER_PAUSE_TTL_SECONDS"
PAUSE_STATE_FILE = "staff_scheduler_pause.json"
DEFAULT_PAUSE_TTL_SECONDS = 72 * 3600.0
_ALERT_DEBOUNCE_SECONDS = 300.0
_last_alert_at: dict[str, float] = {}
_alert_lock = threading.Lock()


@dataclass(frozen=True)
class PauseStatus:
    """Snapshot for schedule/board payloads and watchdog alerts."""

    applies: bool
    pause_until: str | None
    expired: bool
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "applies": self.applies,
            "pause_until": self.pause_until,
            "expired": self.expired,
            "source": self.source,
        }


def _pause_state_path() -> Path:
    return Path(os.environ.get("STAFF_SCHEDULER_PAUSE_STATE", str(_config_dir() / PAUSE_STATE_FILE))).expanduser()


def _parse_iso(raw: str) -> datetime | None:
    text = raw.strip()
    if not text:
        return None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _default_ttl_seconds() -> float:
    raw = os.environ.get(PAUSE_TTL_ENV, "").strip()
    if not raw:
        return DEFAULT_PAUSE_TTL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_PAUSE_TTL_SECONDS
    return max(60.0, value)


def _load_pause_state() -> dict[str, Any]:
    path = _pause_state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_pause_state(data: dict[str, Any]) -> None:
    path = _pause_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def clear_pause_state() -> None:
    """Drop persisted pause metadata when the scheduler is enabled again."""
    path = _pause_state_path()
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("staff scheduler pause: could not clear state file %s: %s", path, exc)


def pause_applies(scheduler_enabled: bool) -> bool:
    return MACHINE_ROLE == "hub" and not scheduler_enabled


def evaluate_pause(scheduler_enabled: bool, now: datetime | None = None) -> PauseStatus:
    """Resolve pause_until and whether the intentional hub pause has expired."""
    cur = now or datetime.now(UTC)
    if not pause_applies(scheduler_enabled):
        if scheduler_enabled:
            clear_pause_state()
        return PauseStatus(applies=False, pause_until=None, expired=False, source="not_applicable")

    env_until = _parse_iso(os.environ.get(PAUSE_UNTIL_ENV, ""))
    if env_until is not None:
        pause_until = env_until
        source = "env"
    else:
        state = _load_pause_state()
        raw_until = state.get("pause_until")
        parsed_until = _parse_iso(str(raw_until or "")) if raw_until else None
        if parsed_until is None:
            pause_until = cur + timedelta(seconds=_default_ttl_seconds())
            _save_pause_state(
                {
                    "paused_at": cur.isoformat().replace("+00:00", "Z"),
                    "pause_until": pause_until.isoformat().replace("+00:00", "Z"),
                    "ttl_seconds": _default_ttl_seconds(),
                }
            )
            source = "state_ttl"
        else:
            pause_until = parsed_until
            source = "state"

    until_iso = pause_until.isoformat().replace("+00:00", "Z")
    expired = cur >= pause_until
    return PauseStatus(applies=True, pause_until=until_iso, expired=expired, source=source)


def pause_expired_alert(now: datetime | None = None) -> dict[str, Any] | None:
    """Build a fleet watchdog alert when the hub pause TTL has elapsed."""
    status = evaluate_pause(scheduler_enabled=False, now=now)
    if not status.applies or not status.expired:
        return None
    return {
        "key": "scheduler-pause-expired",
        "severity": "warning",
        "title": "Staff scheduler pause TTL expired",
        "detail": (
            f"STAFF_SCHEDULER_ENABLED=0 on hub past pause_until {status.pause_until} "
            f"(source={status.source}). Re-enable scheduling or extend STAFF_SCHEDULER_PAUSE_UNTIL."
        ),
        "node": None,
    }


def emit_pause_expired_alert(now: datetime | None = None) -> bool:
    """Record fleet event + push notification at most once per debounce window."""
    alert = pause_expired_alert(now=now)
    if alert is None:
        return False
    cur = now or datetime.now(UTC)
    key = str(alert.get("key") or "")
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
            severity=str(alert.get("severity") or "warning"),  # type: ignore[arg-type]
            kind="watchdog",
            title=str(alert.get("title") or "Scheduler pause expired"),
            detail=str(alert.get("detail") or ""),
            node=alert.get("node"),
        )
    )
    push.notify(
        "staff.escalation",
        str(alert.get("title") or "Scheduler pause expired"),
        str(alert.get("detail") or ""),
    )
    return True


def reset_pause_alert_debounce_for_tests() -> None:
    with _alert_lock:
        _last_alert_at.clear()
