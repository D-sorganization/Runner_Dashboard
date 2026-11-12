"""Host CPU, RAM, and PSI I/O-pressure admission gate for local staff dispatch (#3553)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import psutil
import system_utils
from fastapi import HTTPException
from identity import Principal, format_caller
from staff.audit import AuditError, record_audit

log = logging.getLogger("dashboard.staff")

DEFAULT_DISPATCH_CPU_MAX_PCT = 85.0
DEFAULT_DISPATCH_MEM_MAX_PCT = 85.0
DEFAULT_DISPATCH_IO_PRESSURE_MAX_PCT = 35.0

_ORIG_PSUTIL_CPU_PERCENT = getattr(psutil, "cpu_percent", None)
_ORIG_PSUTIL_VIRTUAL_MEMORY = getattr(psutil, "virtual_memory", None)
_ORIG_GET_IO_PRESSURE_SNAPSHOT = getattr(system_utils, "get_io_pressure_snapshot", None)


@dataclass(frozen=True)
class DispatchResourceSnapshot:
    """One-shot host CPU, memory, and PSI I/O-pressure reading for dispatch admission (#3553)."""

    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    io_pressure_full_avg10: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "cpu_percent": round(float(self.cpu_percent), 2),
            "mem_percent": round(float(self.mem_percent), 2),
            "io_pressure_full_avg10": round(float(self.io_pressure_full_avg10), 2),
        }


def _env_threshold(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        val = float(raw)
    except ValueError:
        log.warning("staff: invalid %s=%r; using default %.1f", name, raw, default)
        return default
    return val if val > 0 else default


def _extract_io_pressure_avg10(snapshot: dict[str, Any] | None) -> float:
    if not isinstance(snapshot, dict):
        return 0.0
    full_block = snapshot.get("full")
    if isinstance(full_block, dict) and full_block.get("avg10") is not None:
        return float(full_block["avg10"])
    if snapshot.get("full_avg10") is not None:
        return float(snapshot["full_avg10"])
    return 0.0


def sample_dispatch_resources() -> DispatchResourceSnapshot:
    """Sample local CPU %, memory %, and PSI full I/O pressure avg10 (#3553)."""
    in_pytest = "PYTEST_CURRENT_TEST" in os.environ
    enforce_live = os.environ.get("STAFF_DISPATCH_RESOURCE_GATE_ENFORCE_IN_TESTS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    cpu_fn = getattr(psutil, "cpu_percent", None)
    mem_fn = getattr(psutil, "virtual_memory", None)
    io_fn = getattr(system_utils, "get_io_pressure_snapshot", None)

    cpu_pct = 0.0
    if callable(cpu_fn) and (not in_pytest or enforce_live or cpu_fn is not _ORIG_PSUTIL_CPU_PERCENT):
        try:
            cpu_pct = float(cpu_fn(interval=None))
        except Exception as exc:  # noqa: BLE001
            log.debug("staff: cpu_percent sample failed: %s", exc)

    mem_pct = 0.0
    if callable(mem_fn) and (not in_pytest or enforce_live or mem_fn is not _ORIG_PSUTIL_VIRTUAL_MEMORY):
        try:
            mem_pct = float(mem_fn().percent)
        except Exception as exc:  # noqa: BLE001
            log.debug("staff: virtual_memory sample failed: %s", exc)

    io_pct = 0.0
    if callable(io_fn) and (not in_pytest or enforce_live or io_fn is not _ORIG_GET_IO_PRESSURE_SNAPSHOT):
        try:
            io_pct = _extract_io_pressure_avg10(io_fn())
        except Exception as exc:  # noqa: BLE001
            log.debug("staff: io_pressure sample failed: %s", exc)

    return DispatchResourceSnapshot(
        cpu_percent=cpu_pct,
        mem_percent=mem_pct,
        io_pressure_full_avg10=io_pct,
    )


def check_dispatch_resource_admission(
    cmd: Any,
    caller: Principal,
    *,
    machine: str = "local",
    sampler: Any = None,
) -> None:
    """Reject local non-dry-run dispatches when CPU, RAM, or I/O pressure is saturated (#3553)."""
    if cmd.dry_run or machine != "local":
        return
    gate_flag = os.environ.get("STAFF_DISPATCH_RESOURCE_GATE", "1").strip().lower()
    if gate_flag in ("0", "false", "no", "off"):
        return

    max_cpu = _env_threshold("STAFF_DISPATCH_CPU_MAX_PCT", DEFAULT_DISPATCH_CPU_MAX_PCT)
    max_mem = _env_threshold("STAFF_DISPATCH_MEM_MAX_PCT", DEFAULT_DISPATCH_MEM_MAX_PCT)
    max_io = _env_threshold("STAFF_DISPATCH_IO_PRESSURE_MAX_PCT", DEFAULT_DISPATCH_IO_PRESSURE_MAX_PCT)

    snap = (sampler or sample_dispatch_resources)()
    breaches: list[str] = []
    if snap.cpu_percent >= max_cpu:
        breaches.append(f"cpu={snap.cpu_percent:.1f}%>={max_cpu:.1f}%")
    if snap.mem_percent >= max_mem:
        breaches.append(f"mem={snap.mem_percent:.1f}%>={max_mem:.1f}%")
    if snap.io_pressure_full_avg10 >= max_io:
        breaches.append(f"io_pressure={snap.io_pressure_full_avg10:.1f}%>={max_io:.1f}%")

    if not breaches:
        return

    thresholds = {
        "cpu_max_pct": max_cpu,
        "mem_max_pct": max_mem,
        "io_pressure_max_pct": max_io,
    }
    summary = ", ".join(breaches)
    log.warning("staff: dispatch of role=%s rejected due to resource saturation (%s)", cmd.role, summary)
    try:
        record_audit(
            action="dispatch",
            target=f"role:{cmd.role}",
            principal=format_caller(caller),
            on_behalf_of=cmd.on_behalf_of,
            surface=cmd.surface,
            thread_id=cmd.thread_id,
            outcome="rejected",
            detail={
                "code": "resource_saturated",
                "breaches": breaches,
                "metrics": snap.to_dict(),
                "thresholds": thresholds,
            },
            fail_closed=False,
        )
    except AuditError as exc:
        log.debug("staff: resource saturation audit skipped: %s", exc)

    raise HTTPException(
        status_code=503,
        detail={
            "code": "resource_saturated",
            "failure_class": "resource_exhausted",
            "message": f"local host resource saturation ({summary}); dispatch rejected",
            "retryable": True,
            "breaches": breaches,
            "metrics": snap.to_dict(),
            "thresholds": thresholds,
        },
    )
