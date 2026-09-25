"""Stalled-job detection and remediation playbooks for the Maintenance role (SC-E5, Issue #1322).

Identifies stuck GitHub Actions jobs and runners, automatically executes low-risk
remediations, and creates proposals in the Maintenance thread for medium/high-risk actions.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from identity import Principal
from staff.actions import (
    ACTION_REGISTRY,
    ActionContext,
    can_auto_execute,
    registered_risk,
)
from staff.audit import record_audit
from staff.conversations import get_conversation_store
from staff.maintenance import execute_maintenance, verify_maintenance

log = logging.getLogger("dashboard.staff.maintenance_detect")

UTC = getattr(_dt, "UTC", _dt.UTC)


class DetectionType(StrEnum):
    QUEUED_TOO_LONG = "queued_too_long"
    RUNNING_PAST_P95 = "running_past_p95"
    WEDGED_LISTENER = "wedged_listener"
    RUNNER_OFFLINE_ASSIGNED_JOB = "runner_offline_assigned_job"
    GHOST_RUNNER = "ghost_runner"


@dataclass
class DetectionItem:
    """A detected anomaly or stalled condition in the fleet."""

    type: DetectionType | str
    severity: str
    target: str
    description: str
    details: dict[str, Any] = field(default_factory=dict)
    recommended_action: str = ""
    action_params: dict[str, Any] = field(default_factory=dict)
    risk_class: str = "medium"
    id: str = field(default_factory=lambda: f"det_{uuid.uuid4().hex[:12]}")
    created_at: str = field(default_factory=lambda: _dt.datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StalledJobDetectionReport:
    """Consolidated report produced by a stalled-job detection scan."""

    timestamp: str = field(default_factory=lambda: _dt.datetime.now(UTC).isoformat())
    detections: list[DetectionItem] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    auto_executed: list[dict[str, Any]] = field(default_factory=list)
    proposals_created: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "detections": [d.to_dict() for d in self.detections],
            "errors": list(self.errors),
            "auto_executed": list(self.auto_executed),
            "proposals_created": list(self.proposals_created),
            "summary": self.summary,
        }


def _parse_iso(val: str | None) -> _dt.datetime | None:
    if not val:
        return None
    try:
        cleaned = str(val).replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return None


# ── DETECTORS ───────────────────────────────────────────────────────────────


def detect_queued_too_long(
    queued_runs: list[dict[str, Any]],
    runners: list[dict[str, Any]],
    queued_threshold_minutes: int = 30,
) -> list[DetectionItem]:
    """Flag queued runs waiting past threshold while matching online runners are idle."""
    now = _dt.datetime.now(UTC)
    results: list[DetectionItem] = []

    # Identify online and non-busy runners with their labels
    idle_runners: list[dict[str, Any]] = [
        r for r in runners if r.get("status") == "online" and not bool(r.get("busy", False))
    ]

    for run in queued_runs:
        created_dt = _parse_iso(run.get("created_at"))
        if not created_dt:
            continue
        age_mins = (now - created_dt).total_seconds() / 60.0
        if age_mins < queued_threshold_minutes:
            continue

        req_labels = set(run.get("labels") or [])
        matching_idle = [r for r in idle_runners if not req_labels or req_labels.issubset(set(r.get("labels") or []))]

        if matching_idle:
            repo = str(run.get("repo") or "unknown")
            run_id = int(run.get("id") or 0)
            target = f"{repo}#{run_id}"
            results.append(
                DetectionItem(
                    type=DetectionType.QUEUED_TOO_LONG,
                    severity="low",
                    target=target,
                    description=f"Run queued for {int(age_mins)}m with {len(matching_idle)} matching idle runner(s)",
                    details={
                        "repo": repo,
                        "run_id": run_id,
                        "age_minutes": round(age_mins, 1),
                        "workflow_name": run.get("workflow_name", ""),
                        "idle_runners": [r.get("name") for r in matching_idle],
                    },
                    recommended_action="maintenance.cancel_and_rerun",
                    action_params={
                        "repo": repo,
                        "run_id": run_id,
                        "failed_only": False,
                    },
                    risk_class="low",
                )
            )

    return results


def detect_running_past_p95(
    in_progress_runs: list[dict[str, Any]],
    workflow_p95_minutes: dict[tuple[str, str], float] | None = None,
    default_p95_minutes: float = 30.0,
    multiplier: float = 3.0,
) -> list[DetectionItem]:
    """Flag running jobs whose duration exceeds workflow p95 * 3."""
    now = _dt.datetime.now(UTC)
    p95_map = workflow_p95_minutes or {}
    results: list[DetectionItem] = []

    for run in in_progress_runs:
        start_str = run.get("run_started_at") or run.get("created_at")
        start_dt = _parse_iso(start_str)
        if not start_dt:
            continue
        elapsed_mins = (now - start_dt).total_seconds() / 60.0

        repo = str(run.get("repo") or "unknown")
        wf = str(run.get("workflow_name") or "")
        p95 = p95_map.get((repo, wf), default_p95_minutes)
        threshold = p95 * multiplier

        if elapsed_mins > threshold:
            run_id = int(run.get("id") or 0)
            target = f"{repo}#{run_id}"
            results.append(
                DetectionItem(
                    type=DetectionType.RUNNING_PAST_P95,
                    severity="medium",
                    target=target,
                    description=f"Run active for {int(elapsed_mins)}m exceeding p95 threshold ({int(threshold)}m)",
                    details={
                        "repo": repo,
                        "run_id": run_id,
                        "workflow_name": wf,
                        "elapsed_minutes": round(elapsed_mins, 1),
                        "p95_minutes": p95,
                        "threshold_minutes": threshold,
                    },
                    recommended_action="maintenance.run_cancel",
                    action_params={"repo": repo, "run_id": run_id},
                    risk_class="medium",
                )
            )

    return results


def detect_wedged_listener(
    runners: list[dict[str, Any]],
    stale_seconds: float = 600.0,
    check_fn: Callable[[dict[str, Any]], float | None] | None = None,
) -> list[DetectionItem]:
    """Flag online runners whose listener log has not updated within stale_seconds."""
    now_ts = time.time()
    results: list[DetectionItem] = []

    for runner in runners:
        if runner.get("status") != "online":
            continue

        name = str(runner.get("name") or "unknown")
        host = str(runner.get("host") or "local")

        age_sec: float | None = None
        if check_fn:
            age_sec = check_fn(runner)
        else:
            log_path = runner.get("listener_log_path")
            if log_path and os.path.isfile(log_path):
                mtime = os.path.getmtime(log_path)
                age_sec = max(0.0, now_ts - mtime)

        if age_sec is not None and age_sec > stale_seconds:
            results.append(
                DetectionItem(
                    type=DetectionType.WEDGED_LISTENER,
                    severity="low",
                    target=name,
                    description=f"Runner '{name}' online but listener log stale by {int(age_sec)}s",
                    details={
                        "runner_name": name,
                        "host": host,
                        "log_age_seconds": round(age_sec, 1),
                    },
                    recommended_action="maintenance.runner_restart",
                    action_params={"runner_name": name, "host": host},
                    risk_class="low",
                )
            )

    return results


def detect_runner_offline_assigned_job(
    runners: list[dict[str, Any]],
    in_progress_jobs_or_runs: list[dict[str, Any]],
) -> list[DetectionItem]:
    """Flag offline runners that currently have assigned in-progress jobs."""
    results: list[DetectionItem] = []
    offline_runners = {r.get("name"): r for r in runners if r.get("status") == "offline"}

    for run in in_progress_jobs_or_runs:
        assigned = run.get("assigned_runner_name") or run.get("runner_name")
        if assigned and assigned in offline_runners:
            repo = str(run.get("repo") or "unknown")
            run_id = int(run.get("id") or 0)
            target = f"{assigned}:{repo}#{run_id}"
            results.append(
                DetectionItem(
                    type=DetectionType.RUNNER_OFFLINE_ASSIGNED_JOB,
                    severity="medium",
                    target=target,
                    description=f"Runner '{assigned}' is offline with active job {repo}#{run_id}",
                    details={"runner_name": assigned, "repo": repo, "run_id": run_id},
                    recommended_action="maintenance.run_cancel",
                    action_params={"repo": repo, "run_id": run_id},
                    risk_class="medium",
                )
            )

    return results


def detect_ghost_runners(
    runners: list[dict[str, Any]],
    known_hosts: set[str] | None = None,
    offline_threshold_days: float = 7.0,
) -> list[DetectionItem]:
    """Flag dead or unknown ghost runner registrations."""
    results: list[DetectionItem] = []

    for runner in runners:
        name = str(runner.get("name") or "unknown")
        host = str(runner.get("host") or "")
        status = runner.get("status")

        is_unknown_host = known_hosts is not None and host and host not in known_hosts
        offline_days = float(runner.get("offline_days") or 0.0)
        is_long_offline = status == "offline" and offline_days >= offline_threshold_days

        if is_unknown_host or is_long_offline:
            reason = "unknown host" if is_unknown_host else f"offline {int(offline_days)}d"
            results.append(
                DetectionItem(
                    type=DetectionType.GHOST_RUNNER,
                    severity="high",
                    target=name,
                    description=f"Ghost runner registration '{name}' ({reason})",
                    details={
                        "runner_name": name,
                        "runner_id": runner.get("id"),
                        "host": host,
                        "offline_days": offline_days,
                    },
                    recommended_action="maintenance.runner_remove",
                    action_params={
                        "runner_name": name,
                        "host": host,
                        "runner_id": runner.get("id"),
                    },
                    risk_class="high",
                )
            )

    return results


# ── ORCHESTRATOR ────────────────────────────────────────────────────────────


class StalledJobDetector:
    """Orchestrates detectors, isolated execution, auto-remediations, and proposal creation."""

    def __init__(self) -> None:
        self.conv_store = get_conversation_store()

    def _collect_detections(
        self,
        queued_runs: list[dict[str, Any]] | None = None,
        in_progress_runs: list[dict[str, Any]] | None = None,
        runners: list[dict[str, Any]] | None = None,
        known_hosts: set[str] | None = None,
        workflow_p95: dict[tuple[str, str], float] | None = None,
    ) -> tuple[list[DetectionItem], list[dict[str, Any]]]:
        """Run all 5 detectors with isolated exception handling per detector."""
        q_runs = queued_runs or []
        ip_runs = in_progress_runs or []
        r_list = runners or []

        all_detections: list[DetectionItem] = []
        errors: list[dict[str, Any]] = []

        detectors: list[tuple[str, Callable[[], list[DetectionItem]]]] = [
            ("detect_queued_too_long", lambda: detect_queued_too_long(q_runs, r_list)),
            (
                "detect_running_past_p95",
                lambda: detect_running_past_p95(ip_runs, workflow_p95),
            ),
            ("detect_wedged_listener", lambda: detect_wedged_listener(r_list)),
            (
                "detect_runner_offline_assigned_job",
                lambda: detect_runner_offline_assigned_job(r_list, ip_runs),
            ),
            ("detect_ghost_runners", lambda: detect_ghost_runners(r_list, known_hosts)),
        ]

        for name, fn in detectors:
            try:
                items = fn()
                all_detections.extend(items)
            except Exception as exc:  # noqa: BLE001
                log.error("Stalled detector probe %s failed: %s", name, exc, exc_info=True)
                errors.append({"detector": name, "error": str(exc)})

        return all_detections, errors

    def _ensure_maintenance_thread(self) -> str:
        """Find or create the dedicated Maintenance role conversation thread."""
        threads = self.conv_store.list_threads()
        for th in threads:
            if "maintenance" in th.participants or th.title == "Fleet Maintenance":
                return th.id

        created = self.conv_store.create_thread(
            title="Fleet Maintenance",
            role="maintenance",
            created_by="maintenance_detector",
        )
        return created.id

    def run_scan(
        self,
        queued_runs: list[dict[str, Any]] | None = None,
        in_progress_runs: list[dict[str, Any]] | None = None,
        runners: list[dict[str, Any]] | None = None,
        known_hosts: set[str] | None = None,
        workflow_p95: dict[tuple[str, str], float] | None = None,
        auto_remediate: bool = True,
        caller: Principal | None = None,
    ) -> StalledJobDetectionReport:
        """Execute detection scan, auto-remediate low-risk, and propose medium/high-risk."""
        detections, errors = self._collect_detections(
            queued_runs=queued_runs,
            in_progress_runs=in_progress_runs,
            runners=runners,
            known_hosts=known_hosts,
            workflow_p95=workflow_p95,
        )

        auto_executed: list[dict[str, Any]] = []
        proposals_created: list[dict[str, Any]] = []

        thread_id = self._ensure_maintenance_thread()
        caller_prin = caller or Principal(id="maintenance_detector", name="Maintenance", type="service")

        for det in detections:
            action_name = det.recommended_action
            if not action_name:
                continue

            # Risk comes from the registered action, never from the detection: detections are
            # built from runner and job data, which must not be able to downgrade a gate (#1344).
            action_def = ACTION_REGISTRY.get(action_name)
            risk = registered_risk(action_name)
            if auto_remediate and action_def and can_auto_execute(action_def, "maintenance", caller_prin):
                # Auto-execute low-risk remediation
                ctx = ActionContext(thread_id=thread_id, caller=caller_prin)
                try:
                    res = execute_maintenance(action_name, det.action_params, ctx)
                    v_ok, v_msg = verify_maintenance(res, det.action_params, ctx, action_name)
                    res.verification_ok = v_ok
                    res.verification_message = v_msg
                    auto_executed.append(
                        {
                            "action": action_name,
                            "target": det.target,
                            "success": res.success,
                            "verification_ok": v_ok,
                            "result": res.result,
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("Auto-remediation for %s failed: %s", det.target, exc)
                    auto_executed.append(
                        {
                            "action": action_name,
                            "target": det.target,
                            "success": False,
                            "error": str(exc),
                        }
                    )
            else:
                # Propose medium or high-risk remediation in Maintenance thread
                msg = self.conv_store.add_message(
                    thread_id=thread_id,
                    author_kind="role",
                    author="maintenance",
                    body_md=f"**Detection**: {det.description}\n\n*Recommended action*: `{action_name}`",
                )
                try:
                    prop = self.conv_store.create_proposal(
                        message_id=msg.id,
                        thread_id=thread_id,
                        action=action_name,
                        params=det.action_params,
                        risk=risk,
                        principal="maintenance",
                    )
                    proposals_created.append(prop.to_dict())
                except Exception as exc:  # noqa: BLE001
                    log.warning("Failed to create proposal for %s: %s", det.target, exc)

        summary = (
            f"Detected {len(detections)} anomaly(ies); {len(auto_executed)} auto-remediated; "
            f"{len(proposals_created)} proposal(s) created; {len(errors)} error(s)."
        )

        # Record SC-A8 audit entry
        record_audit(
            action="maintenance",
            target="stalled_detection_scan",
            principal="maintenance_detector",
            surface="scheduler",
            thread_id=thread_id,
            outcome="success" if not errors else "partial_failure",
            detail={
                "detections_count": len(detections),
                "auto_executed_count": len(auto_executed),
                "proposals_count": len(proposals_created),
                "errors_count": len(errors),
            },
        )

        return StalledJobDetectionReport(
            detections=detections,
            errors=errors,
            auto_executed=auto_executed,
            proposals_created=proposals_created,
            summary=summary,
        )
