"""Hysteretic host CPU/RAM pressure guard for runner-scheduler (#4311).

Reads the #4309 ``host_metrics.json`` cache only — never probes Windows on the tick
path. Stale or missing cache never sheds capacity.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

HOST_PRESSURE_LATCH_PATH = Path(
    os.environ.get("RUNNER_HOST_PRESSURE_LATCH", "/var/lib/runner-scheduler/host-pressure.json")
)
HOST_METRICS_STALE_S = float(os.environ.get("RUNNER_HOST_METRICS_STALE_S", "90"))
_CACHE_BASENAME = "host_metrics.json"


def _default_state_dir() -> Path:
    override = os.environ.get("RUNNER_DASHBOARD_STATE_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".config" / "runner-dashboard"


def host_metrics_cache_path() -> Path:
    return _default_state_dir() / _CACHE_BASENAME


def _parse_sampled_at(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def read_fresh_host_metrics(*, now: float | None = None) -> dict[str, Any] | None:
    """Return cache payload when fresh; ``None`` when missing, unreadable, or stale."""
    path = host_metrics_cache_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    sampled_at = _parse_sampled_at(payload.get("sampled_at"))
    if sampled_at is None:
        return None
    ts = now if now is not None else time.time()
    age = max(0.0, ts - sampled_at.timestamp())
    if age > HOST_METRICS_STALE_S:
        return None
    return payload


def validate_host_pressure(host: object) -> None:
    if host is None:
        return
    if not isinstance(host, dict):
        raise ValueError("resource_floor.host must be an object")
    for key in (
        "max_host_cpu_percent",
        "max_host_ram_percent",
        "sustain_seconds",
        "recovery_seconds",
        "min_online",
        "cooldown_seconds",
    ):
        if key not in host:
            continue
        value = host[key]
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            raise ValueError(f"resource_floor.host.{key} must be a nonnegative number")
    min_online = int(host.get("min_online", 2))
    if min_online < 0:
        raise ValueError("resource_floor.host.min_online must be >= 0")


def _coerce_host_percent(raw: object, *, field: str) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    value = float(raw)
    if not math.isfinite(value) or value < 0 or value > 100:
        log.warning("host metrics %s invalid (%r); ignoring", field, raw)
        return None
    return value


def _windows_metrics(payload: dict[str, Any]) -> tuple[float | None, float | None]:
    windows = payload.get("windows")
    if not isinstance(windows, dict):
        return None, None
    cpu_f = _coerce_host_percent(windows.get("cpu_percent"), field="windows.cpu_percent")
    mem_f = _coerce_host_percent(windows.get("memory_percent"), field="windows.memory_percent")
    return cpu_f, mem_f


def _breach_now(host: dict[str, Any], cpu: float | None, mem: float | None) -> bool:
    max_cpu = float(host.get("max_host_cpu_percent", 85))
    max_ram = float(host.get("max_host_ram_percent", 80))
    cpu_hit = cpu is not None and math.isfinite(cpu) and 0 <= cpu <= 100 and cpu >= max_cpu
    mem_hit = mem is not None and math.isfinite(mem) and 0 <= mem <= 100 and mem >= max_ram
    return cpu_hit or mem_hit


def _load_latch() -> dict[str, Any]:
    if not HOST_PRESSURE_LATCH_PATH.is_file():
        return {}
    try:
        data = json.loads(HOST_PRESSURE_LATCH_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_latch(state: dict[str, Any]) -> None:
    if not state:
        if HOST_PRESSURE_LATCH_PATH.exists():
            HOST_PRESSURE_LATCH_PATH.unlink(missing_ok=True)
        return
    try:
        HOST_PRESSURE_LATCH_PATH.parent.mkdir(parents=True, exist_ok=True)
        HOST_PRESSURE_LATCH_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        log.warning("host-pressure latch not written (%s): %s", HOST_PRESSURE_LATCH_PATH, exc)


def apply_host_pressure_guard(
    floor: dict[str, Any],
    desired: int,
    reason: str,
    *,
    now: float | None = None,
) -> tuple[int, str]:
    """Gradually shed at most one runner per tick after a sustained breach (#4311)."""
    host = floor.get("host")
    if not isinstance(host, dict) or not host:
        return desired, reason

    metrics = read_fresh_host_metrics(now=now)
    if metrics is None:
        return desired, reason

    cpu, mem = _windows_metrics(metrics)
    if cpu is None and mem is None:
        return desired, reason

    ts = now if now is not None else time.time()
    min_online = max(0, int(host.get("min_online", 2)))
    sustain = float(host.get("sustain_seconds", 180))
    recovery = float(host.get("recovery_seconds", 300))
    cooldown = float(host.get("cooldown_seconds", 60))
    breach = _breach_now(host, cpu, mem)

    state = _load_latch()
    pressure_active = bool(state.get("pressure_active"))
    cap = state.get("cap")

    if breach:
        state["recovery_since"] = None
        if state.get("breach_since") is None:
            state["breach_since"] = ts
        breach_age = ts - float(state["breach_since"])
        if breach_age >= sustain:
            if not pressure_active:
                state["pressure_active"] = True
                cap = max(min_online, desired - 1)
            else:
                prev = int(cap) if cap is not None else desired
                cap = max(min_online, prev - 1)
            state["cap"] = cap
            state["last_shed_at"] = ts
        _save_latch(state)
        if state.get("pressure_active") and cap is not None:
            capped = min(desired, int(cap))
            detail = f"cpu={cpu} mem={mem}" if cpu is not None or mem is not None else "host"
            return capped, f"{reason} + host-pressure ({detail})"
        return desired, reason

    state["breach_since"] = None
    if pressure_active:
        last_shed = float(state.get("last_shed_at") or 0)
        if ts - last_shed < cooldown:
            _save_latch(state)
            capped = min(desired, int(cap)) if cap is not None else desired
            return capped, f"{reason} + host-pressure (cooldown)"
        if state.get("recovery_since") is None:
            state["recovery_since"] = ts
        recovery_age = ts - float(state["recovery_since"])
        if recovery_age >= recovery:
            state = {}
            _save_latch(state)
            return desired, reason
        _save_latch(state)
        capped = min(desired, int(cap)) if cap is not None else desired
        return capped, f"{reason} + host-pressure (recovery-hold)"

    if state:
        _save_latch({})
    return desired, reason
