"""Expiring runner target override (#4308). Deploy twin: ``deploy/runner_target_contract.py``."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from machine_registry import build_machine_registry_index, load_machine_registry

log = logging.getLogger("dashboard.runner_target")

# Normalized fleet host tokens (public examples: hub-a, node-b, node-a).
DEFAULT_HOST_CAPS: dict[str, int] = {
    "huba": 6,
    "nodeb": 6,
    "nodea": 8,
}

TARGET_OVERRIDE_PATH = Path(os.environ.get("RUNNER_TARGET_OVERRIDE", "/var/lib/runner-scheduler/target-override.json"))


def normalize_host_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def default_host_cap(host: str | None) -> int:
    token = normalize_host_token(host or "")
    return DEFAULT_HOST_CAPS.get(token, 16)


def host_runner_cap(host: str) -> int:
    """Effective cap is ``min(registry runners.max, hard panel cap)`` (#4308 / #4307)."""
    hard_cap = default_host_cap(host)
    token = normalize_host_token(host)
    try:
        index = build_machine_registry_index(load_machine_registry())
        entry = index.get(token)
        if isinstance(entry, dict):
            runners = entry.get("runners")
            if isinstance(runners, dict):
                maximum = runners.get("max")
                if isinstance(maximum, int) and maximum >= 0:
                    return min(maximum, hard_cap)
    except Exception as exc:  # noqa: BLE001 - cap lookup is advisory
        log.debug("host cap registry lookup failed for %s: %s", host, exc)
    return hard_cap


def clamp_runners(requested: int, host: str | None, cap: int | None = None) -> int:
    maximum = cap if cap is not None else host_runner_cap(host or "")
    return max(0, min(maximum, int(requested)))


def parse_until(value: object, *, now: datetime | None = None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("until must be a non-empty ISO timestamp or HH:MM")
    text = value.strip()
    if re.match(r"^\d{2}:\d{2}$", text):
        anchor = now or datetime.now(UTC)
        hour, minute = (int(part) for part in text.split(":", 1))
        local = anchor.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if local <= anchor:
            local = local + timedelta(days=1)
        return local
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("until must be ISO-8601 or HH:MM") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _parse_stored_until(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def load_target_override(
    path: Path | None = None,
    *,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any] | None:
    target_path = path or TARGET_OVERRIDE_PATH
    if not target_path.is_file():
        return None
    try:
        payload = json.loads(target_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    until_raw = payload.get("until")
    if until_raw:
        clock = now() if now else datetime.now(UTC)
        until = _parse_stored_until(until_raw)
        if until is None:
            try:
                until = parse_until(until_raw, now=clock)
            except ValueError:
                until = None
        if until and clock >= until:
            clear_target_override(target_path)
            return None
    return payload


def write_target_override(
    *,
    runners: int,
    reason: str,
    actor: str,
    before: int,
    until: str | None = None,
    path: Path | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    target_path = path or TARGET_OVERRIDE_PATH
    created = created_at or datetime.now(UTC)
    record = {
        "runners": int(runners),
        "until": until,
        "reason": reason,
        "actor": actor,
        "before": int(before),
        "created_at": created.isoformat(),
    }
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = target_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target_path)
    return record


def clear_target_override(path: Path | None = None) -> bool:
    target_path = path or TARGET_OVERRIDE_PATH
    try:
        target_path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def format_until_for_storage(until: str | None, *, now: datetime | None = None) -> str | None:
    """Normalize caller ``until`` to stored ISO when parseable."""
    if until is None:
        return None
    parsed = parse_until(until, now=now)
    return parsed.isoformat() if parsed else until.strip()
