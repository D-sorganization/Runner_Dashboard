"""Per-node provider cooldown after capacity / quota / rate-limit failures (#2047).

Cooldowns are durable: they are persisted as JSON under the dashboard config dir
(``STAFF_PROVIDER_COOLDOWNS_FILE`` overrides) with wall-clock UTC expiry, so a hub
restart does not forget that a provider is rate-limited or quota-exhausted.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from staff.provider_failover_config import failover_cooldown_seconds
from staff.store import _config_dir

log = logging.getLogger("dashboard.staff.provider_cooldown")

COOLDOWNS_FILE = "staff_provider_cooldowns.json"
_FILE_VERSION = 1

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
_LOADED = False


@dataclass
class _CooldownState:
    failure_class: str
    until_epoch: float  # wall-clock UTC seconds; monotonic time is meaningless across restarts
    until_iso: str
    reason: str


def cooldown_file_path() -> Path:
    """Durable cooldown store: ``STAFF_PROVIDER_COOLDOWNS_FILE`` or the dashboard config dir."""
    override = os.environ.get("STAFF_PROVIDER_COOLDOWNS_FILE")
    return Path(override).expanduser() if override else _config_dir() / COOLDOWNS_FILE


def _expired(state: _CooldownState) -> bool:
    return time.time() >= state.until_epoch


def _state_from_row(row: Any) -> _CooldownState | None:
    if not isinstance(row, dict):
        return None
    try:
        until = datetime.fromisoformat(str(row["until"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    return _CooldownState(
        failure_class=str(row.get("failure_class", "")),
        until_epoch=until.timestamp(),
        until_iso=until.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        reason=str(row.get("reason", ""))[:300],
    )


def _read_file() -> dict[str, _CooldownState]:
    """Tolerant load: missing/corrupt file yields ``{}``; expired entries are dropped."""
    path = cooldown_file_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        log.warning("cannot read provider cooldowns %s: %s", path, exc)
        return {}
    try:
        rows = json.loads(raw)["cooldowns"]
        if not isinstance(rows, dict):
            raise TypeError("cooldowns is not an object")
    except (ValueError, KeyError, TypeError) as exc:
        log.warning("ignoring corrupt provider cooldown file %s: %s", path, exc)
        return {}
    out: dict[str, _CooldownState] = {}
    for pid, row in rows.items():
        state = _state_from_row(row)
        if state is not None and not _expired(state):
            out[str(pid)] = state
    return out


def _write_file_locked() -> None:
    """Atomically persist ``_COOLDOWNS`` (tmp + replace); caller holds ``_LOCK``. Never raises."""
    path = cooldown_file_path()
    payload = {
        "version": _FILE_VERSION,
        "cooldowns": {
            pid: {"failure_class": s.failure_class, "until": s.until_iso, "reason": s.reason}
            for pid, s in _COOLDOWNS.items()
        },
    }
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("cannot persist provider cooldowns to %s: %s", path, exc)
        tmp.unlink(missing_ok=True)


def _ensure_loaded_locked() -> None:
    global _LOADED
    if _LOADED:
        return
    _COOLDOWNS.clear()
    _COOLDOWNS.update(_read_file())
    _LOADED = True
    if cooldown_file_path().exists():
        _write_file_locked()  # rewrite so pruned/corrupt content does not linger


def reload_provider_cooldowns() -> None:
    """Drop in-memory state; the next access re-reads the durable file (restart / tests)."""
    global _LOADED
    with _LOCK:
        _LOADED = False
        _COOLDOWNS.clear()


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
    """Mark ``provider`` cooling down on this node (durably); returns UTC end time."""
    pid = provider.lower().strip()
    until = cooldown_until_from_message(failure_class, message)
    state = _CooldownState(
        failure_class=failure_class,
        until_epoch=until.timestamp(),
        until_iso=until.isoformat().replace("+00:00", "Z"),
        reason=(message or failure_class)[:300],
    )
    with _LOCK:
        _ensure_loaded_locked()
        _COOLDOWNS[pid] = state
        _write_file_locked()
    log.info("provider %s cooling down until %s (%s)", pid, state.until_iso, failure_class)
    return until


def _active_state_locked(pid: str) -> _CooldownState | None:
    _ensure_loaded_locked()
    state = _COOLDOWNS.get(pid)
    if state is None:
        return None
    if _expired(state):
        _COOLDOWNS.pop(pid, None)
        _write_file_locked()
        return None
    return state


def provider_in_cooldown(provider: str) -> bool:
    pid = provider.lower().strip()
    with _LOCK:
        return _active_state_locked(pid) is not None


def provider_cooldown_until(provider: str) -> str | None:
    pid = provider.lower().strip()
    with _LOCK:
        state = _active_state_locked(pid)
        return state.until_iso if state else None


def earliest_cooldown_reset() -> str | None:
    """ISO timestamp of the soonest active cooldown end on this node."""
    with _LOCK:
        _ensure_loaded_locked()
        active = [s for s in _COOLDOWNS.values() if not _expired(s)]
    if not active:
        return None
    return min(s.until_iso for s in active)


def cooldown_snapshot() -> list[dict[str, Any]]:
    """Serializable cooldown rows for ``/api/staff/summary``."""
    rows: list[dict[str, Any]] = []
    with _LOCK:
        _ensure_loaded_locked()
        for pid in list(_COOLDOWNS):
            state = _active_state_locked(pid)
            if state is None:
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
    """Clear all cooldowns, in memory and on disk."""
    global _LOADED
    with _LOCK:
        _COOLDOWNS.clear()
        _LOADED = True
        _write_file_locked()
