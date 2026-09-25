"""Unit tests for Stalled-Job Detection and Remediation Playbooks (SC-E5, Issue #1322)."""

from __future__ import annotations

import datetime as _dt
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from identity import Principal
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.maintenance import (
    reset_maintenance_cooldowns,
)
from staff.maintenance_detect import (
    DetectionItem,
    DetectionType,
    StalledJobDetectionReport,
    StalledJobDetector,
    detect_ghost_runners,
    detect_queued_too_long,
    detect_runner_offline_assigned_job,
    detect_running_past_p95,
    detect_wedged_listener,
)

UTC = getattr(_dt, "UTC", _dt.UTC)
TEST_OPERATOR = Principal(id="operator-1", name="Operator", type="human", roles=["operator", "owner"])


@pytest.fixture(autouse=True)
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    reset_conversation_store()
    reset_maintenance_cooldowns()
    db_file = tmp_path / "detect_test.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    yield
    reset_conversation_store()
    reset_maintenance_cooldowns()


# ── 1. DETECTOR FIXTURES & TESTS ─────────────────────────────────────────────


def test_detect_queued_too_long_with_idle_matching_runners() -> None:
    """Queued run exceeding threshold with available matching idle runners is detected."""
    now = _dt.datetime.now(UTC)
    old_time = (now - _dt.timedelta(minutes=45)).isoformat()
    recent_time = (now - _dt.timedelta(minutes=5)).isoformat()

    queued_runs = [
        {
            "id": 101,
            "repo": "UpstreamDrift",
            "workflow_name": "ci.yml",
            "status": "queued",
            "created_at": old_time,
            "labels": ["self-hosted", "linux"],
        },
        {
            "id": 102,
            "repo": "Tools",
            "workflow_name": "build.yml",
            "status": "queued",
            "created_at": recent_time,
            "labels": ["self-hosted", "linux"],
        },
    ]

    # Matching online runner that is idle (not busy)
    runners = [
        {
            "name": "runner-linux-1",
            "status": "online",
            "busy": False,
            "labels": ["self-hosted", "linux", "x64"],
        }
    ]

    detections = detect_queued_too_long(queued_runs, runners, queued_threshold_minutes=30)
    assert len(detections) == 1
    d = detections[0]
    assert d.type == DetectionType.QUEUED_TOO_LONG
    assert d.target == "UpstreamDrift#101"
    assert d.recommended_action == "maintenance.cancel_and_rerun"
    assert d.risk_class == "low"
    assert d.details["age_minutes"] >= 45


def test_detect_queued_too_long_no_idle_runners_not_flagged() -> None:
    """Queued run exceeding threshold is NOT flagged if all matching runners are busy."""
    now = _dt.datetime.now(UTC)
    old_time = (now - _dt.timedelta(minutes=45)).isoformat()

    queued_runs = [
        {
            "id": 103,
            "repo": "UpstreamDrift",
            "workflow_name": "ci.yml",
            "status": "queued",
            "created_at": old_time,
            "labels": ["gpu"],
        }
    ]
    # Runner exists but is busy
    runners = [
        {
            "name": "gpu-runner-1",
            "status": "online",
            "busy": True,
            "labels": ["gpu"],
        }
    ]

    detections = detect_queued_too_long(queued_runs, runners, queued_threshold_minutes=30)
    assert len(detections) == 0


def test_detect_running_past_p95() -> None:
    """Running job exceeding workflow p95 * 3 is flagged as stalled."""
    now = _dt.datetime.now(UTC)
    # Started 100 minutes ago; p95 is 20 minutes; 20 * 3 = 60 minutes threshold
    started_at = (now - _dt.timedelta(minutes=100)).isoformat()

    in_progress_runs = [
        {
            "id": 201,
            "repo": "UpstreamDrift",
            "workflow_name": "tests.yml",
            "status": "in_progress",
            "run_started_at": started_at,
        }
    ]
    workflow_p95 = {("UpstreamDrift", "tests.yml"): 20.0}

    detections = detect_running_past_p95(
        in_progress_runs,
        workflow_p95_minutes=workflow_p95,
        default_p95_minutes=30.0,
        multiplier=3.0,
    )
    assert len(detections) == 1
    d = detections[0]
    assert d.type == DetectionType.RUNNING_PAST_P95
    assert d.target == "UpstreamDrift#201"
    assert d.recommended_action == "maintenance.run_cancel"
    assert d.risk_class == "medium"
    assert d.details["elapsed_minutes"] >= 100


def test_detect_wedged_listener_by_log_mtime(tmp_path: Path) -> None:
    """Online runner with stale listener log mtime is flagged as wedged listener."""
    # Create simulated listener log with old mtime
    log_file = tmp_path / "runner_listener.log"
    log_file.write_text("Listening for jobs...", encoding="utf-8")
    old_mtime = time.time() - 900  # 15 minutes ago
    import os

    os.utime(log_file, (old_mtime, old_mtime))

    runners = [
        {
            "name": "runner-wedged-1",
            "status": "online",
            "host": "node-1",
            "listener_log_path": str(log_file),
        }
    ]

    detections = detect_wedged_listener(runners, stale_seconds=600.0)
    assert len(detections) == 1
    d = detections[0]
    assert d.type == DetectionType.WEDGED_LISTENER
    assert d.target == "runner-wedged-1"
    assert d.recommended_action == "maintenance.runner_restart"
    assert d.risk_class == "low"
    assert d.details["log_age_seconds"] >= 900


def test_detect_runner_offline_with_assigned_job() -> None:
    """Offline runner with an active assigned job is detected."""
    runners = [
        {
            "name": "offline-host-runner",
            "status": "offline",
            "host": "node-2",
        }
    ]
    runs = [
        {
            "id": 301,
            "repo": "Tools",
            "status": "in_progress",
            "assigned_runner_name": "offline-host-runner",
        }
    ]

    detections = detect_runner_offline_assigned_job(runners, runs)
    assert len(detections) == 1
    d = detections[0]
    assert d.type == DetectionType.RUNNER_OFFLINE_ASSIGNED_JOB
    assert d.recommended_action == "maintenance.run_cancel"
    assert d.risk_class == "medium"
    assert d.details["runner_name"] == "offline-host-runner"


def test_detect_ghost_runner_registrations() -> None:
    """Runner not present in known machines or long-offline is flagged as ghost."""
    runners = [
        {
            "id": 401,
            "name": "ghost-unknown-host",
            "status": "offline",
            "host": "defunct-machine",
            "offline_days": 14.0,
        },
        {
            "id": 402,
            "name": "known-active-runner",
            "status": "online",
            "host": "known-node-1",
            "offline_days": 0.0,
        },
    ]
    known_hosts = {"known-node-1"}

    detections = detect_ghost_runners(runners, known_hosts=known_hosts, offline_threshold_days=7.0)
    assert len(detections) == 1
    d = detections[0]
    assert d.type == DetectionType.GHOST_RUNNER
    assert d.recommended_action == "maintenance.runner_remove"
    assert d.risk_class == "high"


# ── 2. ISOLATION & PLAYBOOK EXECUTION TESTS ─────────────────────────────────


def test_detector_exceptions_isolated_per_detector() -> None:
    """Detector failure in one probe does not stop others; errors are captured."""
    detector = StalledJobDetector()

    # Fault injection: wedged listener detector raises an error
    def faulty_wedged(*a: Any, **kw: Any) -> list[DetectionItem]:
        raise PermissionError("Access denied reading listener log directory")

    with patch("staff.maintenance_detect.detect_wedged_listener", side_effect=faulty_wedged):
        report = detector.run_scan(
            queued_runs=[],
            in_progress_runs=[],
            runners=[],
            auto_remediate=False,
        )

        assert isinstance(report, StalledJobDetectionReport)
        assert len(report.errors) == 1
        assert "Access denied" in report.errors[0]["error"]
        assert report.errors[0]["detector"] == "detect_wedged_listener"


def test_simulated_wedged_listener_restarted_and_verified() -> None:
    """Wedged listener: runner_restart is a registered medium-risk action, so the detector
    proposes it (a "low" detection label cannot downgrade the gate, #1344); once approved
    it restarts and verifies the runner is back online."""
    from staff.actions import execute_proposal

    detector = StalledJobDetector()

    wedged_detection = DetectionItem(
        type=DetectionType.WEDGED_LISTENER,
        severity="low",
        target="runner-wedged-1",
        description="Listener log mtime stale by 900s",
        details={"runner_name": "runner-wedged-1", "host": "local"},
        recommended_action="maintenance.runner_restart",
        action_params={"runner_name": "runner-wedged-1", "host": "local"},
        risk_class="low",
    )

    with patch.object(detector, "_collect_detections", return_value=([wedged_detection], [])):
        report = detector.run_scan(auto_remediate=True)

    assert len(report.detections) == 1
    assert report.auto_executed == []
    assert len(report.proposals_created) == 1
    proposal = report.proposals_created[0]
    assert (proposal["action"], proposal["risk"]) == ("maintenance.runner_restart", "medium")

    # Simulated runner backend: the restart succeeds and the runner reports online again.
    with (
        patch("staff.maintenance._get_runner_state", return_value={"busy": False, "status": "online"}),
        patch("staff.maintenance._run_service_command", return_value=(0, "restarted", "")),
    ):
        res = execute_proposal(proposal["id"], approver=TEST_OPERATOR)

    assert res.success is True
    assert res.verification_ok is True


def test_high_risk_remediation_waits_for_approval() -> None:
    """High-risk action (ghost runner removal or host shutdown) is NOT auto-executed."""
    detector = StalledJobDetector()

    ghost_detection = DetectionItem(
        type=DetectionType.GHOST_RUNNER,
        severity="high",
        target="ghost-runner-old",
        description="Ghost runner registration offline 20 days",
        details={"runner_name": "ghost-runner-old", "host": "old-host"},
        recommended_action="maintenance.runner_remove",
        action_params={"runner_name": "ghost-runner-old", "host": "old-host"},
        risk_class="high",
    )

    with patch.object(detector, "_collect_detections", return_value=([ghost_detection], [])):
        report = detector.run_scan(auto_remediate=True)

        # High risk must NOT be auto-executed
        assert len(report.auto_executed) == 0
        # Instead, an action proposal must be created in the Maintenance thread
        assert len(report.proposals_created) == 1
        proposal = report.proposals_created[0]
        assert proposal["action"] == "maintenance.runner_remove"
        assert proposal["risk"] == "high"
        assert proposal["state"] == "proposed"

        store = get_conversation_store()
        th = store.get_thread(proposal["thread_id"])
        assert th is not None
        assert "maintenance" in th.participants
