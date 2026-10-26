"""Slot-health detector (#2898)."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from fleet_events import FleetEvent, get_event_store
from fleet_events import Severity as FleetEventSeverity

TOOLCACHE_MARKERS = (
    "actions/setup-python",
    "setup-python",
    "python-versions manifest",
    "tool-cache",
    "toolcache",
)
_FAILURE_CONCLUSIONS = frozenset({"failure", "failed", "timed_out", "cancelled"})
Severity = Literal["warning", "critical"]


class SlotHealthPreconditionError(ValueError):
    """Invalid job snapshot input."""


@dataclass(frozen=True, slots=True)
class SlotFlag:
    descriptor: str
    job_name: str
    reasons: tuple[str, ...]
    severity: Severity
    sample_size: int
    slot_rate: float
    fleet_rate: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "descriptor": self.descriptor,
            "job_name": self.job_name,
            "reasons": list(self.reasons),
            "severity": self.severity,
            "sample_size": self.sample_size,
            "slot_rate": round(self.slot_rate, 4),
            "fleet_rate": round(self.fleet_rate, 4),
        }


def describe_slot(labels: Sequence[str]) -> str:
    os_name = next((label for label in labels if label in ("Linux", "Windows", "macOS")), "unknown-OS")
    fleet = [label for label in labels if label == "d-sorg-fleet" or label.startswith("d-sorg-fleet-")]
    tier = next((label for label in fleet if label != "d-sorg-fleet"), "generic-fleet")
    return f"{os_name} fleet runner ({tier})"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _bad_outcome(job: Mapping[str, Any]) -> bool:
    conclusion = str(job.get("conclusion") or "").lower()
    return conclusion in _FAILURE_CONCLUSIONS or conclusion == "timed_out"


def _toolcache_hit(job: Mapping[str, Any]) -> bool:
    blob = " ".join(str(job.get(key) or "") for key in ("log_excerpt", "step_name", "error_message", "message")).lower()
    return any(marker in blob for marker in TOOLCACHE_MARKERS)


def _window_jobs(jobs: Sequence[Mapping[str, Any]], now: datetime, window_hours: int) -> list[Mapping[str, Any]]:
    cutoff = now - timedelta(hours=window_hours)
    kept: list[Mapping[str, Any]] = []
    for job in jobs:
        ts = _parse_ts(str(job.get("completed_at") or job.get("started_at") or ""))
        if ts is None or ts < cutoff:
            continue
        kept.append(job)
    return kept


def detect_slot_health_flags(
    jobs: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    window_hours: int = 24,
    rate_multiplier: float = 3.0,
    min_samples: int = 5,
) -> list[SlotFlag]:
    if window_hours < 1:
        raise SlotHealthPreconditionError("window_hours must be >= 1")
    now = now or datetime.now(UTC)
    scoped = _window_jobs(jobs, now, window_hours)
    if not scoped:
        return []

    fleet_bad: dict[str, int] = defaultdict(int)
    fleet_total: dict[str, int] = defaultdict(int)
    for job in scoped:
        name = str(job.get("job_name") or "unknown")
        fleet_total[name] += 1
        if _bad_outcome(job):
            fleet_bad[name] += 1

    slot_bad: dict[tuple[str, str], int] = defaultdict(int)
    slot_total: dict[tuple[str, str], int] = defaultdict(int)
    slot_toolcache: dict[tuple[str, str], int] = defaultdict(int)

    for job in scoped:
        labels = [str(x) for x in job.get("runner_labels") or job.get("labels") or [] if x]
        descriptor = describe_slot(labels)
        job_name = str(job.get("job_name") or "unknown")
        key = (descriptor, job_name)
        slot_total[key] += 1
        if _bad_outcome(job):
            slot_bad[key] += 1
        if _toolcache_hit(job):
            slot_toolcache[key] += 1

    flags: list[SlotFlag] = []
    for key, total in slot_total.items():
        descriptor, job_name = key
        bad = slot_bad[key]
        fleet_rate = fleet_bad[job_name] / fleet_total[job_name] if fleet_total[job_name] else 0.0
        slot_rate = bad / total if total else 0.0
        reasons: list[str] = []
        if slot_toolcache[key] >= 1:
            reasons.append("toolcache/setup-python signature")
        if total >= min_samples and fleet_rate > 0 and slot_rate >= rate_multiplier * fleet_rate:
            reasons.append(
                f"{job_name} bad-outcome rate {slot_rate:.0%} >= {rate_multiplier:.0f}x fleet "
                f"{fleet_rate:.0%} (n={total})"
            )
        elif total >= min_samples and fleet_rate == 0 and bad == total:
            reasons.append(f"{job_name} bad-outcome rate 100% with n={total}")
        if reasons:
            flags.append(
                SlotFlag(
                    descriptor=descriptor,
                    job_name=job_name,
                    reasons=tuple(reasons),
                    severity="critical" if slot_toolcache[key] else "warning",
                    sample_size=total,
                    slot_rate=slot_rate,
                    fleet_rate=fleet_rate,
                )
            )
    return flags


def analyze_jobs(
    jobs: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    window_hours: int = 24,
) -> dict[str, Any]:
    flags = detect_slot_health_flags(jobs, now=now, window_hours=window_hours)
    return {
        "window_hours": window_hours,
        "jobs_considered": len(_window_jobs(jobs, now or datetime.now(UTC), window_hours)),
        "flags": [flag.to_dict() for flag in flags],
    }


def publish_slot_health_flags(
    flags: Sequence[Mapping[str, Any]],
    *,
    event_store: Any | None = None,
    barb_lane: Callable[[Sequence[Mapping[str, Any]]], None] | None = None,
) -> int:
    if not flags:
        return 0
    store = event_store or get_event_store()
    ts = int(time.time() * 1000)
    for flag in flags:
        store.record(
            FleetEvent(
                ts=ts,
                severity=cast(
                    FleetEventSeverity,
                    flag.get("severity") or "warning",
                ),
                kind="slot_health",
                title=f"Slot health flag: {flag['descriptor']}",
                detail="; ".join(flag.get("reasons") or []),
            )
        )
    lane = barb_lane or _default_barb_lane
    lane(flags)
    return len(flags)


def _default_barb_lane(flags: Sequence[Mapping[str, Any]]) -> None:
    from staff.audit import record_audit

    record_audit(
        action="slot_health.flags",
        principal="hub",
        target="barb-operator-lane",
        detail={"count": len(flags), "descriptors": [f.get("descriptor") for f in flags]},
    )
