"""Tests for Stalled-Job Detection and Remediation Playbooks (SC-E5, Issue #1322)."""

from __future__ import annotations

import datetime as _dt
import time
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from identity import Principal
from staff.actions import (
    ActionContext,
    ActionRiskClass,
)
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.maintenance import reset_maintenance_cooldowns
from staff.maintenance_detect import (
    DetectionIssue,
    DetectorScanResult,
    detect_ghost_runners,
    detect_offline_with_job,
    detect_queued_too_long,
    detect_running_past_p95,
    detect_wedged_listener,
    run_maintenance_detectors,
)
from staff.maintenance_playbooks import (
    SelfHealThrottleTracker,
    get_or_create_maintenance_thread,
    remediate_issue,
    reset_self_heal_throttle,
)
from staff.work_items import WorkItemStore

UTC = getattr(_dt, "UTC", _dt.UTC)


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    reset_conversation_store()
    reset_maintenance_cooldowns()
    reset_self_heal_throttle()
    db_file = tmp_path / "maintenance_detect_test.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    yield
    reset_conversation_store()
    reset_maintenance_cooldowns()
    reset_self_heal_throttle()


TEST_CALLER = Principal(id="barb", name="Barb", type="agent", roles=["secretary", "admin"])
TEST_CTX = ActionContext(caller=TEST_CALLER, thread_id="test-thread-1")


# ─── Detector 1: Queued Too Long with Idle Matching Runners ───────────────────


def test_detect_queued_too_long_with_idle_matching_runners() -> None:
    now = time.time()
    runs = [
        {
            "id": 101,
            "repo": "Runner_Dashboard",
            "status": "queued",
            "created_at_ts": now - 3600,  # 60m ago
            "labels": ["self-hosted", "linux"],
        }
    ]
    runners = [
        {
            "name": "runner-linux-1",
            "status": "online",
            "busy": False,
            "labels": ["self-hosted", "linux", "x64"],
            "host": "host-alpha",
        }
    ]
    issues = detect_queued_too_long(runs, runners, threshold_minutes=30.0, current_ts=now)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.detector == "queued_too_long"
    assert issue.severity == "low"
    assert issue.self_heal_eligible is True
    assert issue.target == "Runner_Dashboard#101"
    assert issue.host == "host-alpha"


def test_detect_queued_too_long_ignores_unmatched_or_busy_runners() -> None:
    now = time.time()
    runs = [
        {
            "id": 102,
            "repo": "Runner_Dashboard",
            "status": "queued",
            "created_at_ts": now - 3600,
            "labels": ["gpu", "cuda"],
        }
    ]
    # No matching GPU runners
    runners = [
        {
            "name": "runner-linux-1",
            "status": "online",
            "busy": False,
            "labels": ["self-hosted", "linux"],
            "host": "host-alpha",
        }
    ]
    issues = detect_queued_too_long(runs, runners, threshold_minutes=30.0, current_ts=now)
    assert len(issues) == 0


# ─── Detector 2: Running Past p95 x 3 ─────────────────────────────────────────


def test_detect_running_past_p95() -> None:
    now = time.time()
    runs = [
        {
            "id": 201,
            "repo": "Repo_A",
            "status": "in_progress",
            "started_at_ts": now - (40 * 60),  # 40m running
            "p95_minutes": 10.0,  # 3 * p95 = 30m
            "host": "host-beta",
        }
    ]
    issues = detect_running_past_p95(runs, current_ts=now)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.detector == "running_past_p95"
    assert issue.severity == "medium"
    assert issue.self_heal_eligible is False  # Must be approved by human/operator
    assert issue.risk_class == ActionRiskClass.MEDIUM
    assert issue.target == "Repo_A#201"


# ─── Detector 3: Wedged Listener (Stale Log mtime) ───────────────────────────


def test_detect_wedged_listener_stale_mtime() -> None:
    now = time.time()
    runners = [
        {
            "name": "runner-stuck",
            "status": "online",
            "busy": True,
            "host": "host-gamma",
            "listener_log_mtime": now - 600,  # 10m ago (stale > 300s)
        }
    ]
    issues = detect_wedged_listener(runners, stale_seconds=300.0, current_ts=now)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.detector == "wedged_listener"
    assert issue.severity == "low"
    assert issue.self_heal_eligible is True
    assert issue.suggested_action == "maintenance.runner_restart"
    assert issue.target == "runner-stuck"
    assert issue.host == "host-gamma"


def test_detect_wedged_listener_fresh_mtime_not_flagged() -> None:
    now = time.time()
    runners = [
        {
            "name": "runner-healthy",
            "status": "online",
            "busy": True,
            "host": "host-gamma",
            "listener_log_mtime": now - 30,  # 30s ago (fresh)
        }
    ]
    issues = detect_wedged_listener(runners, stale_seconds=300.0, current_ts=now)
    assert len(issues) == 0


# ─── Detector 4: Runner Offline with Assigned Job ─────────────────────────────


def test_detect_offline_runner_with_assigned_job() -> None:
    runners = [
        {
            "name": "runner-offline-1",
            "status": "offline",
            "assigned_job": {"id": 301, "repo": "Core_Service"},
            "host": "host-delta",
        }
    ]
    issues = detect_offline_with_job(runners)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.detector == "offline_with_job"
    assert issue.severity == "medium"
    assert issue.self_heal_eligible is False
    assert issue.target == "runner-offline-1"


# ─── Detector 5: Ghost Runner Registrations ───────────────────────────────────


def test_detect_ghost_runners() -> None:
    runners = [
        {
            "name": "ghost-runner-99",
            "status": "offline",
            "offline_days": 10.0,
            "host": "unknown-host",
        },
        {
            "name": "untracked-runner",
            "status": "online",
            "offline_days": 0.0,
            "host": "host-zeta",
        },
    ]
    inventory = {"legit-runner-1"}
    issues = detect_ghost_runners(runners, known_inventory=inventory, max_offline_days=7.0)
    assert len(issues) == 2
    detectors = [i.detector for i in issues]
    assert all(d == "ghost_runners" for d in detectors)
    targets = [i.target for i in issues]
    assert "ghost-runner-99" in targets
    assert "untracked-runner" in targets


# ─── Detector Fault Isolation ────────────────────────────────────────────────


def test_detector_scan_fault_isolation() -> None:
    def buggy_detector(*args: Any, **kwargs: Any) -> list[DetectionIssue]:
        raise RuntimeError("GitHub API network timeout simulated")

    with patch("staff.maintenance_detect.detect_wedged_listener", side_effect=buggy_detector):
        now = time.time()
        runs = [
            {
                "id": 501,
                "repo": "Repo_X",
                "status": "queued",
                "created_at_ts": now - 3600,
                "labels": ["self-hosted"],
            }
        ]
        runners = [
            {
                "name": "r1",
                "status": "online",
                "busy": False,
                "labels": ["self-hosted"],
                "host": "host-1",
            }
        ]
        result = run_maintenance_detectors(runs=runs, runners=runners, known_inventory={"r1"}, current_ts=now)
        # Scan completed without raising exception
        assert isinstance(result, DetectorScanResult)
        # wedged_listener failed and was recorded in errors
        assert "wedged_listener" in result.errors
        assert "GitHub API network timeout simulated" in result.errors["wedged_listener"]
        # other detectors ran successfully
        assert any(i.detector == "queued_too_long" for i in result.issues)


# ─── Playbook 1: Self-Heal Wedged Listener ────────────────────────────────────


def test_self_heal_restart_wedged_listener_success() -> None:
    issue = DetectionIssue(
        issue_id="wedged_listener:runner-stuck",
        detector="wedged_listener",
        severity="low",
        target="runner-stuck",
        host="host-gamma",
        details={"log_age_seconds": 600},
        suggested_action="maintenance.runner_restart",
        risk_class=ActionRiskClass.LOW,
        self_heal_eligible=True,
    )

    conv_store = get_conversation_store()
    wi_store = WorkItemStore()

    with patch("staff.maintenance_playbooks.execute_maintenance") as mock_exec:
        from staff.actions import ActionResult

        mock_exec.return_value = ActionResult(
            success=True,
            result={"runner": "runner-stuck", "status": "restarted"},
        )

        res = remediate_issue(
            issue=issue,
            ctx=TEST_CTX,
            conversation_store=conv_store,
            work_item_store=wi_store,
        )

        assert res["status"] == "self_healed"
        assert res["action"] == "maintenance.runner_restart"
        mock_exec.assert_called_once()

        # Verified Maintenance thread message posted
        thread = get_or_create_maintenance_thread(conv_store)
        messages = conv_store.list_messages(thread.id)
        assert len(messages) >= 1
        assert any("Self-Heal" in m.body_md and "runner-stuck" in m.body_md for m in messages)

        # Verified WorkItemStore record in terminal 'done' state
        wi = wi_store.get_work_item(res["work_item_id"])
        assert wi is not None
        assert wi.state == "done"
        assert wi.owner_role == "maintenance"


# ─── Playbook 2: Self-Heal Cancel and Rerun Stale Queued Job ──────────────────


def test_self_heal_cancel_and_rerun_stale_queued_job() -> None:
    issue = DetectionIssue(
        issue_id="queued_too_long:Repo_Y:701",
        detector="queued_too_long",
        severity="low",
        target="Repo_Y#701",
        host="host-alpha",
        details={"repo": "Repo_Y", "run_id": 701},
        suggested_action="maintenance.run_cancel",
        risk_class=ActionRiskClass.LOW,
        self_heal_eligible=True,
    )

    conv_store = get_conversation_store()
    wi_store = WorkItemStore()

    with patch("staff.maintenance_playbooks.execute_maintenance") as mock_exec:
        from staff.actions import ActionResult

        mock_exec.return_value = ActionResult(
            success=True,
            result={"status": "cancelled"},
        )

        res = remediate_issue(
            issue=issue,
            ctx=TEST_CTX,
            conversation_store=conv_store,
            work_item_store=wi_store,
        )

        assert res["status"] == "self_healed"
        # Called cancel and rerun
        assert mock_exec.call_count == 2


# ─── Playbook 3: Throttle Limits (3 per host per hour) ────────────────────────


def test_self_heal_throttle_max_3_per_host_per_hour() -> None:
    throttle = SelfHealThrottleTracker()
    now = time.time()
    host = "host-limited"

    # Execute 3 self-heals on host-limited
    for i in range(3):
        allowed, reason = throttle.can_self_heal(host=host, target=f"runner-{i}", now=now)
        assert allowed is True
        throttle.record_self_heal(host=host, target=f"runner-{i}", action="maintenance.runner_restart", now=now)

    # 4th attempt on same host must be throttled
    allowed, reason = throttle.can_self_heal(host=host, target="runner-4", now=now)
    assert allowed is False
    assert reason == "host_rate_limit_exceeded"

    # Remediation escalates through Barb when throttled
    conv_store = get_conversation_store()
    wi_store = WorkItemStore()

    issue = DetectionIssue(
        issue_id="wedged_listener:runner-4",
        detector="wedged_listener",
        severity="low",
        target="runner-4",
        host=host,
        details={},
        suggested_action="maintenance.runner_restart",
        risk_class=ActionRiskClass.LOW,
        self_heal_eligible=True,
    )

    res = remediate_issue(
        issue=issue,
        ctx=TEST_CTX,
        conversation_store=conv_store,
        work_item_store=wi_store,
        throttle=throttle,
    )

    assert res["status"] == "escalated"
    assert res["reason"] == "host_rate_limit_exceeded"
    assert "proposal_id" in res

    # Work item marked escalated
    wi = wi_store.get_work_item(res["work_item_id"])
    assert wi is not None
    assert wi.state == "escalated"


# ─── Playbook 4: Repeat Failure Escalates Through Barb ────────────────────────


def test_self_heal_repeat_failure_escalates_through_barb() -> None:
    throttle = SelfHealThrottleTracker()
    now = time.time()
    host = "host-repeat"
    target = "runner-flakey"

    # First self-heal passes
    allowed, _ = throttle.can_self_heal(host=host, target=target, now=now)
    assert allowed is True
    throttle.record_self_heal(host=host, target=target, action="maintenance.runner_restart", now=now)

    # Repeat failure on same target within window
    allowed, reason = throttle.can_self_heal(host=host, target=target, now=now + 120)
    assert allowed is False
    assert reason == "repeat_failure"

    conv_store = get_conversation_store()
    wi_store = WorkItemStore()

    issue = DetectionIssue(
        issue_id=f"wedged_listener:{target}",
        detector="wedged_listener",
        severity="low",
        target=target,
        host=host,
        details={},
        suggested_action="maintenance.runner_restart",
        risk_class=ActionRiskClass.LOW,
        self_heal_eligible=True,
    )

    res = remediate_issue(
        issue=issue,
        ctx=TEST_CTX,
        conversation_store=conv_store,
        work_item_store=wi_store,
        throttle=throttle,
    )

    assert res["status"] == "escalated"
    assert res["reason"] == "repeat_failure"
    assert "proposal_id" in res


# ─── Playbook 5: High/Medium Risk Remediation Waits for Approval ──────────────


def test_high_risk_remediation_waits_for_approval() -> None:
    issue = DetectionIssue(
        issue_id="ghost_runners:ghost-runner-99",
        detector="ghost_runners",
        severity="high",
        target="ghost-runner-99",
        host="unknown-host",
        details={"offline_days": 10},
        suggested_action="maintenance.fleet_control",
        risk_class=ActionRiskClass.HIGH,
        self_heal_eligible=False,
    )

    conv_store = get_conversation_store()
    wi_store = WorkItemStore()

    res = remediate_issue(
        issue=issue,
        ctx=TEST_CTX,
        conversation_store=conv_store,
        work_item_store=wi_store,
    )

    assert res["status"] == "proposed"
    assert "proposal_id" in res

    # Proposal created in 'proposed' state, not executed
    proposal = conv_store.get_proposal(res["proposal_id"])
    assert proposal is not None
    assert proposal.state == "proposed"
    assert proposal.risk == ActionRiskClass.HIGH
    assert proposal.action == "maintenance.fleet_control"

    # Work item created in 'open' state
    wi = wi_store.get_work_item(res["work_item_id"])
    assert wi is not None
    assert wi.state == "open"
    assert wi.owner_role == "maintenance"
