"""Per-node provider cooldown after capacity / quota / rate-limit failures (#2047)."""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from staff.provider_failover_config import failover_cooldown_seconds

log = logging.getLogger("dashboard.staff.provider_cooldown")

_RESET_IN_RE = re.compile(
    r"(?:resets?|retry)(?:\s+at|\s+in|\s+after)?\s+(\d{1,4})\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)",
    re.IGNORECASE,
)
_ISO_RESET_RE = re.compile(
    r"(?:resets?|reset)\s+(?:at\s+)?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2})",
    re.IGNORECASE,
)

_LOCK = threading.Lock()
_COOLDOWNS: dict[str, _CooldownState] = {}


@dataclass
class _CooldownState:
    failure_class: str
    until_monotonic: float
    until_iso: str
    reason: str


def _parse_delay_seconds(text: str) -> float | None:
    if not text:
        return None
    m = _RESET_IN_RE.search(text)
    if not m:
        return None
    qty = int(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith("hour") or unit.startswith("hr"):
        return float(qty * 3600)
    if unit.startswith("min"):
        return float(qty * 60)
    return float(qty)


def _parse_iso_reset(text: str) -> datetime | None:
    m = _ISO_RESET_RE.search(text)
    if not m:
        return None
    raw = m.group(1).replace(" ", "T")
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=UTC)
    except ValueError:
        return None


def cooldown_until_from_message(failure_class: str, message: str) -> datetime:
    """Derive cooldown end from failure class defaults or provider message hints."""
    now = datetime.now(UTC)
    parsed_iso = _parse_iso_reset(message)
    if parsed_iso and parsed_iso > now:
        return parsed_iso
    delay = _parse_delay_seconds(message)
    if delay is not None and delay > 0:
        return now + timedelta(seconds=delay)
    seconds = failover_cooldown_seconds().get(failure_class, 600.0)
    return now + timedelta(seconds=seconds)


def mark_provider_cooldown(provider: str, failure_class: str, *, message: str = "") -> datetime:
    """Mark ``provider`` cooling down on this node; returns UTC end time."""
    pid = provider.lower().strip()
    until = cooldown_until_from_message(failure_class, message)
    until_mono = time.monotonic() + max(0.0, (until - datetime.now(UTC)).total_seconds())
    state = _CooldownState(
        failure_class=failure_class,
        until_monotonic=until_mono,
        until_iso=until.isoformat().replace("+00:00", "Z"),
        reason=(message or failure_class)[:300],
    )
    with _LOCK:
        _COOLDOWNS[pid] = state
    log.info("provider %s cooling down until %s (%s)", pid, state.until_iso, failure_class)
    return until


def provider_in_cooldown(provider: str) -> bool:
    pid = provider.lower().strip()
    with _LOCK:
        state = _COOLDOWNS.get(pid)
        if state is None:
            return False
        if time.monotonic() >= state.until_monotonic:
            _COOLDOWNS.pop(pid, None)
            return False
        return True


def provider_cooldown_until(provider: str) -> str | None:
    pid = provider.lower().strip()
    with _LOCK:
        state = _COOLDOWNS.get(pid)
        if state is None:
            return None
        if time.monotonic() >= state.until_monotonic:
            _COOLDOWNS.pop(pid, None)
            return None
        return state.until_iso


def earliest_cooldown_reset() -> str | None:
    """ISO timestamp of the soonest active cooldown end on this node."""
    with _LOCK:
        active = [s for s in _COOLDOWNS.values() if time.monotonic() < s.until_monotonic]
    if not active:
        return None
    return min(s.until_iso for s in active)


def cooldown_snapshot() -> list[dict[str, Any]]:
    """Serializable cooldown rows for ``/api/staff/summary``."""
    now_mono = time.monotonic()
    rows: list[dict[str, Any]] = []
    with _LOCK:
        for pid, state in list(_COOLDOWNS.items()):
            if now_mono >= state.until_monotonic:
                _COOLDOWNS.pop(pid, None)
                continue
            rows.append(
                {
                    "provider": pid,
                    "failure_class": state.failure_class,
                    "until": state.until_iso,
                    "reason": state.reason,
                }
            )
    return sorted(rows, key=lambda r: r["until"])


def reset_provider_cooldowns() -> None:
    with _LOCK:
        _COOLDOWNS.clear()
