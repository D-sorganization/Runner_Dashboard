"""Stalled-job detection across runners and workflows (SC-E5, Issue #1322).

Owner Policy:
Only Self-heal playbooks run automatically:
1. Restart one wedged listener.
2. Cancel and re-run one stale queued job.
3. Clean orphaned worktrees.
Rate-limited to <= 3 per host per hour; repeat failures escalate through Barb.
All other detections become ActionProposals presented by Barb.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from staff.maintenance_playbooks import (
    AutoHealRateTracker,
    MaintenancePlaybookEngine,
    RemediationResult,
)
from staff.store import _now

__all__ = [
    "AutoHealRateTracker",
    "Detection",
    "DetectionKind",
    "MaintenancePlaybookEngine",
    "RemediationResult",
    "detect_ghost_runners",
    "detect_offline_runner_assigned_job",
    "detect_orphaned_worktrees",
    "detect_queued_too_long",
    "detect_running_past_p95",
    "detect_wedged_listener",
    "run_all_detectors",
]

log = logging.getLogger("dashboard.staff.maintenance_detect")


class DetectionKind:
    QUEUED_IDLE_RUNNERS = "queued_idle_runners"
    RUNNING_PAST_P95 = "running_past_p95"
    WEDGED_LISTENER = "wedged_listener"
    OFFLINE_RUNNER_ASSIGNED_JOB = "offline_runner_assigned_job"
    GHOST_RUNNER = "ghost_runner_registration"
    ORPHANED_WORKTREES = "orphaned_worktrees"


@dataclass(frozen=True)
class Detection:
    """Anomaly detected across runners, jobs, or disk resources."""

    kind: str
    target: str
    host: str
    severity: str
    details: dict[str, Any]
    recommended_action: str
    action_params: dict[str, Any]
    auto_heal_eligible: bool
    detected_at: str = field(default_factory=_now)


def _make_det(
    kind: str,
    target: str,
    host: str,
    severity: str,
    details: dict[str, Any],
    action: str,
    params: dict[str, Any],
    auto_heal: bool,
) -> Detection:
    return Detection(
        kind=kind,
        target=target,
        host=host,
        severity=severity,
        details=details,
        recommended_action=action,
        action_params=params,
        auto_heal_eligible=auto_heal,
    )


def detect_queued_too_long(
    queued_runs: list[dict[str, Any]],
    runners: list[dict[str, Any]],
    threshold_minutes: float = 15.0,
    now_ts: float | None = None,
) -> list[Detection]:
    """Detect runs queued longer than threshold when matching idle runners are online."""
    cur_ts = now_ts if now_ts is not None else time.time()
    threshold_sec = threshold_minutes * 60.0
    detections: list[Detection] = []
    online_idle = [r for r in runners if str(r.get("status", "")).lower() == "online" and not bool(r.get("busy"))]
    if not online_idle:
        return detections

    for run in queued_runs:
        if str(run.get("status", "")).lower() != "queued":
            continue
        created = float(run.get("created_at_ts") or 0.0)
        if (cur_ts - created) < threshold_sec:
            continue
        req_labels = set(run.get("labels") or ["self-hosted"])
        matching = [r for r in online_idle if not req_labels or req_labels.issubset(set(r.get("labels") or []))]
        if not matching:
            continue

        repo = str(run.get("repo", "unknown"))
        run_id = int(run.get("id") or run.get("run_id") or 0)
        detections.append(
            _make_det(
                DetectionKind.QUEUED_IDLE_RUNNERS,
                f"{repo}#{run_id}",
                str(matching[0].get("host") or "local"),
                "medium",
                {
                    "repo": repo,
                    "run_id": run_id,
                    "idle_runners": [str(r.get("name")) for r in matching],
                },
                "maintenance.run_rerun",
                {"repo": repo, "run_id": run_id, "failed_only": False},
                True,
            )
        )
    return detections


def detect_running_past_p95(
    running_runs: list[dict[str, Any]],
    workflow_p95_map: dict[str, float] | None = None,
    default_p95_seconds: float = 3600.0,
    multiplier: float = 3.0,
    now_ts: float | None = None,
) -> list[Detection]:
    """Detect runs executing past 3x historical P95 duration."""
    cur_ts = now_ts if now_ts is not None else time.time()
    p95_map = workflow_p95_map or {}
    detections: list[Detection] = []

    for run in running_runs:
        if str(run.get("status", "")).lower() not in ("in_progress", "running"):
            continue
        started = float(run.get("started_at_ts") or 0.0)
        duration = cur_ts - started
        repo = str(run.get("repo", "unknown"))
        wf = str(run.get("workflow", "workflow"))
        run_id = int(run.get("id") or run.get("run_id") or 0)

        p95 = p95_map.get(f"{repo}:{wf}") or p95_map.get(wf) or default_p95_seconds
        limit = p95 * multiplier
        if duration > limit:
            detections.append(
                _make_det(
                    DetectionKind.RUNNING_PAST_P95,
                    f"{repo}#{run_id}",
                    str(run.get("host") or "local"),
                    "high",
                    {
                        "repo": repo,
                        "run_id": run_id,
                        "duration": duration,
                        "limit": limit,
                    },
                    "maintenance.run_cancel",
                    {"repo": repo, "run_id": run_id},
                    False,
                )
            )
    return detections


def detect_wedged_listener(
    runners: list[dict[str, Any]],
    listener_logs: dict[str, float] | None = None,
    threshold_minutes: float = 10.0,
    now_ts: float | None = None,
) -> list[Detection]:
    """Detect runner online/idle whose listener log touch is older than threshold."""
    cur_ts = now_ts if now_ts is not None else time.time()
    threshold_sec = threshold_minutes * 60.0
    logs = listener_logs or {}
    detections: list[Detection] = []

    for r in runners:
        if str(r.get("status", "")).lower() != "online" or bool(r.get("busy")):
            continue
        name = str(r.get("name") or r.get("id"))
        last_mtime = logs.get(name) or float(r.get("listener_log_mtime") or 0.0)
        if last_mtime and (cur_ts - last_mtime) > threshold_sec:
            host = str(r.get("host") or "local")
            detections.append(
                _make_det(
                    DetectionKind.WEDGED_LISTENER,
                    name,
                    host,
                    "high",
                    {"runner": name, "elapsed": cur_ts - last_mtime},
                    "maintenance.runner_restart",
                    {"runner_name": name, "host": host},
                    True,
                )
            )
    return detections


def detect_offline_runner_assigned_job(
    runners: list[dict[str, Any]],
    active_jobs: list[dict[str, Any]],
) -> list[Detection]:
    """Detect active jobs assigned to offline or degraded runners."""
    offline_runners = {
        str(r.get("name") or r.get("id")): r
        for r in runners
        if str(r.get("status", "")).lower() in ("offline", "inactive", "degraded")
    }
    detections: list[Detection] = []
    for job in active_jobs:
        if str(job.get("status", "")).lower() not in (
            "in_progress",
            "running",
            "queued",
        ):
            continue
        rname = str(job.get("runner_name") or "")
        matched = offline_runners.get(rname)
        if matched:
            repo = str(job.get("repo", "unknown"))
            run_id = int(job.get("run_id") or 0)
            detections.append(
                _make_det(
                    DetectionKind.OFFLINE_RUNNER_ASSIGNED_JOB,
                    f"{repo}#{run_id}",
                    str(matched.get("host") or "local"),
                    "high",
                    {"repo": repo, "run_id": run_id, "runner": rname},
                    "maintenance.run_cancel",
                    {"repo": repo, "run_id": run_id},
                    False,
                )
            )
    return detections


def detect_ghost_runners(
    runners: list[dict[str, Any]],
    known_hosts: set[str] | list[str] | None = None,
    heartbeats: dict[str, float] | None = None,
    threshold_minutes: float = 30.0,
    now_ts: float | None = None,
) -> list[Detection]:
    """Detect runners whose host is missing from registry or has expired heartbeat."""
    cur_ts = now_ts if now_ts is not None else time.time()
    hosts = set(known_hosts or [])
    hb = heartbeats or {}
    threshold_sec = threshold_minutes * 60.0
    detections: list[Detection] = []

    for r in runners:
        name = str(r.get("name") or r.get("id"))
        host = str(r.get("host") or "unknown")
        is_ghost = False
        reason = ""
        if hosts and host not in hosts:
            is_ghost = True
            reason = f"Host '{host}' not in known machine registry"
        elif hb and (host not in hb or (cur_ts - hb[host]) > threshold_sec):
            is_ghost = True
            reason = f"Host '{host}' heartbeat missing/expired"

        if is_ghost:
            detections.append(
                _make_det(
                    DetectionKind.GHOST_RUNNER,
                    name,
                    host,
                    "medium",
                    {"runner": name, "host": host, "reason": reason},
                    "maintenance.diagnose",
                    {"target": name, "host": host},
                    False,
                )
            )
    return detections


def detect_orphaned_worktrees(
    worktrees: list[dict[str, Any]],
    threshold_days: float = 7.0,
    now_ts: float | None = None,
) -> list[Detection]:
    """Detect orphaned worktrees past retention threshold."""
    cur_ts = now_ts if now_ts is not None else time.time()
    threshold_sec = threshold_days * 86400.0
    stale = [wt for wt in worktrees if (cur_ts - float(wt.get("mtime") or 0.0)) > threshold_sec]
    if not stale:
        return []
    return [
        Detection(
            kind=DetectionKind.ORPHANED_WORKTREES,
            target="worktrees",
            host="local",
            severity="low",
            details={"stale_count": len(stale)},
            recommended_action="maintenance.trim_worktrees",
            action_params={},
            auto_heal_eligible=True,
        )
    ]


def run_all_detectors(
    detector_list: list[tuple[str, Callable[[], list[Detection]]]],
) -> tuple[list[Detection], list[dict[str, Any]]]:
    """Execute all detectors with exception isolation."""
    detections: list[Detection] = []
    errors: list[dict[str, Any]] = []
    for name, fn in detector_list:
        try:
            detections.extend(fn())
        except Exception as exc:
            log.exception("Detector '%s' failed: %s", name, exc)
            errors.append({"detector": name, "error": str(exc), "failure_class": "detector_error"})
    return detections, errors
