"""Stalled-job and wedged-runner detection for the Maintenance role (SC-E5, Issue #1322).

Detects stuck GitHub Actions runs and runners across five diagnostic vectors:
1. Queued too long with idle matching runners
2. Running past the workflow's p95 duration × 3
3. Wedged runner listener (online in GitHub, stale listener log mtime)
4. Offline runner with an assigned in-progress or queued job
5. Ghost runner registrations (registered in GitHub, missing from fleet inventory)

Fault isolation: All detectors execute in isolated exception boundaries so one
detector's failure never prevents the remaining scan from executing.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

UTC = getattr(_dt, "UTC", _dt.UTC)
log = logging.getLogger("dashboard.staff.maintenance_detect")

DEFAULT_QUEUED_THRESHOLD_MINUTES: float = 30.0
DEFAULT_P95_MULTIPLIER: float = 3.0
DEFAULT_P95_FALLBACK_MINUTES: float = 120.0
DEFAULT_LISTENER_STALE_SECONDS: float = 300.0  # 5 minutes
DEFAULT_GHOST_RUNNER_OFFLINE_DAYS: float = 7.0


@dataclass
class DetectionIssue:
    """Represents a discrete operational defect detected across runners or jobs."""

    issue_id: str
    detector: str
    severity: str  # "low", "medium", "high"
    target: str
    host: str
    details: dict[str, Any]
    suggested_action: str
    risk_class: str  # "low", "medium", "high", "owner-only"
    self_heal_eligible: bool
    detected_at: str = field(default_factory=lambda: _dt.datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "detector": self.detector,
            "severity": self.severity,
            "target": self.target,
            "host": self.host,
            "details": self.details,
            "suggested_action": self.suggested_action,
            "risk_class": self.risk_class,
            "self_heal_eligible": self.self_heal_eligible,
            "detected_at": self.detected_at,
        }


@dataclass
class DetectorScanResult:
    """Aggregated output from running all diagnostic detectors."""

    issues: list[DetectionIssue] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    scanned_at: str = field(default_factory=lambda: _dt.datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "issues": [i.to_dict() for i in self.issues],
            "errors": self.errors,
            "scanned_at": self.scanned_at,
        }


def _parse_ts(val: Any) -> float:
    """Convert timestamp float or ISO 8601 string to epoch seconds."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            dt = _dt.datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt.timestamp()
        except Exception:
            return 0.0
    return 0.0


def _labels_match(required: list[str], runner_labels: list[str]) -> bool:
    """Check if runner labels satisfy required job labels (case-insensitive subset)."""
    if not required:
        return True
    runner_set = {str(lbl).lower() for lbl in runner_labels}
    return all(str(r).lower() in runner_set for r in required)


# ─── Detector 1: Queued Too Long with Idle Matching Runners ───────────────────


def detect_queued_too_long(
    runs: list[dict[str, Any]],
    runners: list[dict[str, Any]],
    threshold_minutes: float = DEFAULT_QUEUED_THRESHOLD_MINUTES,
    current_ts: float | None = None,
) -> list[DetectionIssue]:
    """Detect jobs queued > threshold while matching online runners are idle."""
    now = current_ts if current_ts is not None else time.time()
    threshold_sec = threshold_minutes * 60.0
    issues: list[DetectionIssue] = []

    # Identify idle runners
    idle_runners = [r for r in runners if r.get("status") == "online" and not r.get("busy") and r.get("name")]

    for run in runs:
        if run.get("status") != "queued":
            continue
        created_ts = run.get("created_at_ts") or _parse_ts(run.get("created_at"))
        if not created_ts or (now - created_ts) < threshold_sec:
            continue

        req_labels = [str(lbl) for lbl in run.get("labels", []) if lbl]
        matching = [r for r in idle_runners if _labels_match(req_labels, r.get("labels", []))]
        if not matching:
            continue

        repo = str(run.get("repo", "unknown"))
        run_id = run.get("id", 0)
        target = f"{repo}#{run_id}"
        host = matching[0].get("host", "local")
        queued_mins = round((now - created_ts) / 60.0, 1)

        issues.append(
            DetectionIssue(
                issue_id=f"queued_too_long:{repo}:{run_id}",
                detector="queued_too_long",
                severity="low",
                target=target,
                host=host,
                details={
                    "repo": repo,
                    "run_id": run_id,
                    "queued_minutes": queued_mins,
                    "matching_runners": [r.get("name") for r in matching],
                },
                suggested_action="maintenance.run_cancel",
                risk_class="low",
                self_heal_eligible=True,
            )
        )
    return issues


# ─── Detector 2: Running Past p95 × 3 ─────────────────────────────────────────


def detect_running_past_p95(
    runs: list[dict[str, Any]],
    multiplier: float = DEFAULT_P95_MULTIPLIER,
    fallback_minutes: float = DEFAULT_P95_FALLBACK_MINUTES,
    current_ts: float | None = None,
) -> list[DetectionIssue]:
    """Detect runs executing significantly past the workflow's historical p95."""
    now = current_ts if current_ts is not None else time.time()
    issues: list[DetectionIssue] = []

    for run in runs:
        if run.get("status") != "in_progress":
            continue
        start_ts = run.get("started_at_ts") or _parse_ts(run.get("started_at") or run.get("created_at"))
        if not start_ts:
            continue

        elapsed_sec = now - start_ts
        p95_min = float(run.get("p95_minutes") or 0.0)
        cutoff_sec = (p95_min * multiplier * 60.0) if p95_min > 0.0 else (fallback_minutes * 60.0)

        if elapsed_sec > cutoff_sec:
            repo = str(run.get("repo", "unknown"))
            run_id = run.get("id", 0)
            target = f"{repo}#{run_id}"
            running_mins = round(elapsed_sec / 60.0, 1)

            issues.append(
                DetectionIssue(
                    issue_id=f"running_past_p95:{repo}:{run_id}",
                    detector="running_past_p95",
                    severity="medium",
                    target=target,
                    host=str(run.get("host", "local")),
                    details={
                        "repo": repo,
                        "run_id": run_id,
                        "running_minutes": running_mins,
                        "p95_minutes": p95_min,
                        "cutoff_minutes": round(cutoff_sec / 60.0, 1),
                    },
                    suggested_action="maintenance.run_cancel",
                    risk_class="medium",
                    self_heal_eligible=False,
                )
            )
    return issues


# ─── Detector 3: Wedged Listener (Stale Log mtime) ───────────────────────────


def detect_wedged_listener(
    runners: list[dict[str, Any]],
    stale_seconds: float = DEFAULT_LISTENER_STALE_SECONDS,
    current_ts: float | None = None,
) -> list[DetectionIssue]:
    """Detect runner online in GitHub whose local listener log mtime has ceased updating."""
    now = current_ts if current_ts is not None else time.time()
    issues: list[DetectionIssue] = []

    for runner in runners:
        if runner.get("status") != "online":
            continue
        # Only evaluate runners that are busy or marked active
        if not runner.get("busy"):
            continue

        runner_name = str(runner.get("name", ""))
        host = str(runner.get("host", "local"))

        # Explicit mtime timestamp passed in runner dict
        log_mtime = runner.get("listener_log_mtime")
        log_path = runner.get("listener_log_path")

        # Inspect disk file mtime if log_path is provided and log_mtime is not
        if log_mtime is None and log_path and os.path.exists(log_path):
            try:
                log_mtime = os.path.getmtime(log_path)
            except Exception as exc:
                log.debug("Failed reading mtime for %s: %s", log_path, exc)

        if log_mtime is not None:
            age = now - float(log_mtime)
            if age > stale_seconds:
                issues.append(
                    DetectionIssue(
                        issue_id=f"wedged_listener:{runner_name}",
                        detector="wedged_listener",
                        severity="low",
                        target=runner_name,
                        host=host,
                        details={
                            "runner_name": runner_name,
                            "log_age_seconds": round(age, 1),
                            "log_path": log_path,
                        },
                        suggested_action="maintenance.runner_restart",
                        risk_class="low",
                        self_heal_eligible=True,
                    )
                )
    return issues


# ─── Detector 4: Runner Offline with Assigned Job ─────────────────────────────


def detect_offline_with_job(
    runners: list[dict[str, Any]],
) -> list[DetectionIssue]:
    """Detect runners that transitioned offline while an assigned job is unresolved."""
    issues: list[DetectionIssue] = []

    for runner in runners:
        if runner.get("status") != "offline":
            continue
        assigned = runner.get("assigned_job")
        if not assigned:
            continue

        runner_name = str(runner.get("name", ""))
        host = str(runner.get("host", "local"))

        issues.append(
            DetectionIssue(
                issue_id=f"offline_with_job:{runner_name}",
                detector="offline_with_job",
                severity="medium",
                target=runner_name,
                host=host,
                details={
                    "runner_name": runner_name,
                    "assigned_job": assigned,
                },
                suggested_action="maintenance.runner_start",
                risk_class="medium",
                self_heal_eligible=False,
            )
        )
    return issues


# ─── Detector 5: Ghost Runner Registrations ───────────────────────────────────


def detect_ghost_runners(
    runners: list[dict[str, Any]],
    known_inventory: set[str] | None = None,
    max_offline_days: float = DEFAULT_GHOST_RUNNER_OFFLINE_DAYS,
) -> list[DetectionIssue]:
    """Detect orphaned runner registrations missing from fleet inventory or offline > 7 days."""
    issues: list[DetectionIssue] = []
    known = known_inventory if known_inventory is not None else set()

    for runner in runners:
        runner_name = str(runner.get("name", ""))
        if not runner_name:
            continue

        status = runner.get("status", "unknown")
        offline_days = float(runner.get("offline_days") or 0.0)
        host = str(runner.get("host", "unknown"))

        is_untracked = known and runner_name not in known
        is_stale_offline = status == "offline" and offline_days >= max_offline_days

        if is_untracked or is_stale_offline:
            reason = "not_in_inventory" if is_untracked else "offline_stale"
            issues.append(
                DetectionIssue(
                    issue_id=f"ghost_runners:{runner_name}",
                    detector="ghost_runners",
                    severity="high" if is_stale_offline else "medium",
                    target=runner_name,
                    host=host,
                    details={
                        "runner_name": runner_name,
                        "status": status,
                        "offline_days": offline_days,
                        "reason": reason,
                    },
                    suggested_action="maintenance.fleet_control",
                    risk_class="high",
                    self_heal_eligible=False,
                )
            )
    return issues


# ─── Orchestrator: Fault-Isolated Diagnostic Scan ────────────────────────────


def run_maintenance_detectors(
    runs: list[dict[str, Any]],
    runners: list[dict[str, Any]],
    known_inventory: set[str] | None = None,
    current_ts: float | None = None,
    **kwargs: Any,
) -> DetectorScanResult:
    """Execute all diagnostic detectors with isolated exception boundaries."""
    result = DetectorScanResult()

    detectors = [
        (
            "queued_too_long",
            lambda: detect_queued_too_long(
                runs=runs,
                runners=runners,
                threshold_minutes=kwargs.get("queued_threshold_minutes", DEFAULT_QUEUED_THRESHOLD_MINUTES),
                current_ts=current_ts,
            ),
        ),
        (
            "running_past_p95",
            lambda: detect_running_past_p95(
                runs=runs,
                multiplier=kwargs.get("p95_multiplier", DEFAULT_P95_MULTIPLIER),
                fallback_minutes=kwargs.get("p95_fallback_minutes", DEFAULT_P95_FALLBACK_MINUTES),
                current_ts=current_ts,
            ),
        ),
        (
            "wedged_listener",
            lambda: detect_wedged_listener(
                runners=runners,
                stale_seconds=kwargs.get("listener_stale_seconds", DEFAULT_LISTENER_STALE_SECONDS),
                current_ts=current_ts,
            ),
        ),
        (
            "offline_with_job",
            lambda: detect_offline_with_job(runners=runners),
        ),
        (
            "ghost_runners",
            lambda: detect_ghost_runners(
                runners=runners,
                known_inventory=known_inventory,
                max_offline_days=kwargs.get("ghost_runner_offline_days", DEFAULT_GHOST_RUNNER_OFFLINE_DAYS),
            ),
        ),
    ]

    for name, detector_fn in detectors:
        try:
            detected = detector_fn()
            result.issues.extend(detected)
        except Exception as exc:
            log.exception("Maintenance detector '%s' failed", name)
            result.errors[name] = str(exc)

    return result
