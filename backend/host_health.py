"""Per-host fleet health gauge (#4310)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from host_metrics import get_host_metrics_provider

HealthLevel = Literal["green", "amber", "red"]

_DEFAULT_CAPS = {"deskcomputer": 6, "oglaptop": 6, "controltower": 8}


def _default_cap(host: str) -> int:
    token = _normalize_host(host)
    return _DEFAULT_CAPS.get(token, 16)


def _normalize_host(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def _metric_percent(system: dict[str, Any], key: str, nested: str | None = None) -> float | None:
    block = system.get(key) if nested is None else (system.get(key) or {}).get(nested)
    if not isinstance(block, dict):
        return None
    raw = block.get("percent")
    if isinstance(raw, (int, float)):
        return float(raw)
    return None


def _active_schedule_window(capacity: dict[str, Any]) -> dict[str, Any] | None:
    schedule = capacity.get("schedule") if isinstance(capacity, dict) else None
    state = capacity.get("state") if isinstance(capacity, dict) else None
    reason = (state or {}).get("reason") if isinstance(state, dict) else None
    if not isinstance(schedule, dict):
        return None
    tz_name = str(schedule.get("timezone") or "America/Los_Angeles")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("UTC")
    now = datetime.now(tz)
    days = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    today = days[now.weekday()]
    current = now.strftime("%H:%M")
    active: dict[str, Any] | None = None
    for entry in schedule.get("schedules") or []:
        if not isinstance(entry, dict):
            continue
        entry_days = entry.get("days") or []
        if today not in entry_days:
            continue
        start = str(entry.get("start") or "00:00")
        end = str(entry.get("end") or "23:59")
        if start <= end:
            if start <= current < end:
                active = entry
        elif current >= start or current < end:
            active = entry
    if not active and reason and not str(reason).startswith("manual-target"):
        return {"name": reason, "start": "-", "end": "-", "runners": (state or {}).get("desired")}
    if not active:
        return {"name": "default", "runners": schedule.get("default_count")}
    return {
        "name": active.get("name"),
        "start": active.get("start"),
        "end": active.get("end"),
        "runners": active.get("runners"),
    }


def _pick_level(cpu: float | None, mem: float | None, busy: int, online: int, desired: int) -> HealthLevel:
    cpu_v = cpu if cpu is not None else 0.0
    mem_v = mem if mem is not None else 0.0
    if cpu_v >= 90 or mem_v >= 92 or (desired > 0 and online >= desired and busy >= online):
        return "red"
    if cpu_v >= 75 or mem_v >= 85 or (busy > 0 and online > 0 and busy / max(online, 1) >= 0.8):
        return "amber"
    return "green"


def _suggested_action(level: HealthLevel, desired: int, cap: int, until_hint: str | None) -> str | None:
    if level == "green":
        return None
    target = max(2, min(cap, desired - 1))
    until = until_hint or "18:00"
    return f"lower target to {target} until {until}"


def build_host_health_gauge(host: str, system: dict[str, Any]) -> dict[str, Any]:
    capacity = system.get("runner_capacity") if isinstance(system, dict) else {}
    state = (capacity or {}).get("state") or {}
    desired = int(state.get("desired") or capacity.get("configured_runners") or 0)
    online = int(state.get("online") or 0)
    busy = int(state.get("busy") or 0)
    cap = int(capacity.get("max_runners") or _default_cap(host))
    override = state.get("target_override") if isinstance(state, dict) else None
    metrics = get_host_metrics_provider().read_cached(host)
    cpu = _metric_percent(metrics or system, "cpu")
    mem = _metric_percent(metrics or system, "memory")
    if mem is None and isinstance(system.get("memory"), dict):
        mem = _metric_percent(system, "memory")
    if cpu is None and isinstance(system.get("cpu"), dict):
        cpu = _metric_percent(system, "cpu")
    level = _pick_level(cpu, mem, busy, online, desired)
    until_hint = (override or {}).get("until") if isinstance(override, dict) else None
    return {
        "level": level,
        "host": host,
        "desired": desired,
        "online": online,
        "busy": busy,
        "cap": cap,
        "cpu_percent": cpu,
        "memory_percent": mem,
        "metrics_source": "cache" if metrics else "system",
        "schedule_window": _active_schedule_window(capacity or {}),
        "target_override": override,
        "suggested_action": _suggested_action(level, desired, cap, until_hint),
    }
