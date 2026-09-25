"""Unit tests for stalled-job detection and remediation playbooks (SC-E5, Issue #1322)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from identity import Principal
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.maintenance import reset_maintenance_cooldowns
from staff.maintenance_detect import (
    AutoHealRateTracker,
    Detection,
    DetectionKind,
    MaintenancePlaybookEngine,
    detect_ghost_runners,
    detect_offline_runner_assigned_job,
    detect_orphaned_worktrees,
    detect_queued_too_long,
    detect_running_past_p95,
    detect_wedged_listener,
    run_all_detectors,
)
from staff.work_items import get_work_item_store, reset_work_item_store


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    reset_conversation_store()
    reset_work_item_store()
    reset_maintenance_cooldowns()
    db_file = tmp_path / "staff_detect_test.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    yield
    reset_conversation_store()
    reset_work_item_store()
    reset_maintenance_cooldowns()


TEST_OPERATOR = Principal(id="operator", name="Operator", type="human", roles=["admin"])


def test_detect_queued_too_long() -> None:
    now_ts = 1700000000.0
    queued_runs = [
        {
            "id": 101,
            "repo": "org/repo-a",
            "status": "queued",
            "created_at_ts": now_ts - (20 * 60),  # 20 mins ago (threshold 15)
            "labels": ["self-hosted", "linux"],
        },
        {
            "id": 102,
            "repo": "org/repo-b",
            "status": "queued",
            "created_at_ts": now_ts - (5 * 60),  # 5 mins ago
            "labels": ["self-hosted", "linux"],
        },
    ]
    runners = [
        {
            "name": "runner-1",
            "status": "online",
            "busy": False,
            "labels": ["self-hosted", "linux"],
            "host": "node-1",
        },
    ]

    detections = detect_queued_too_long(queued_runs, runners, threshold_minutes=15.0, now_ts=now_ts)
    assert len(detections) == 1
    det = detections[0]
    assert det.kind == DetectionKind.QUEUED_IDLE_RUNNERS
    assert det.target == "org/repo-a#101"
    assert det.auto_heal_eligible is True
    assert det.recommended_action == "maintenance.run_rerun"
    assert det.action_params["run_id"] == 101


def test_detect_running_past_p95() -> None:
    now_ts = 1700000000.0
    runs = [
        {
            "id": 201,
            "repo": "org/repo-a",
            "workflow": "ci.yml",
            "status": "in_progress",
            "started_at_ts": now_ts - 7000,  # running ~116 mins
            "host": "node-1",
        },
        {
            "id": 202,
            "repo": "org/repo-a",
            "workflow": "ci.yml",
            "status": "in_progress",
            "started_at_ts": now_ts - 1000,  # running ~16 mins
            "host": "node-1",
        },
    ]
    workflow_p95_map = {"org/repo-a:ci.yml": 2000.0}  # 3 * 2000 = 6000s threshold

    detections = detect_running_past_p95(runs, workflow_p95_map, multiplier=3.0, now_ts=now_ts)
    assert len(detections) == 1
    det = detections[0]
    assert det.kind == DetectionKind.RUNNING_PAST_P95
    assert det.target == "org/repo-a#201"
    assert det.severity == "high"
    assert det.auto_heal_eligible is False
    assert det.recommended_action == "maintenance.run_cancel"


def test_detect_wedged_listener() -> None:
    now_ts = 1700000000.0
    runners = [
        {"name": "runner-wedged", "status": "online", "busy": False, "host": "node-1"},
        {"name": "runner-active", "status": "online", "busy": True, "host": "node-1"},
        {"name": "runner-healthy", "status": "online", "busy": False, "host": "node-2"},
    ]
    listener_logs = {
        "runner-wedged": now_ts - (15 * 60),  # 15m ago (threshold 10m)
        "runner-healthy": now_ts - (2 * 60),  # 2m ago
    }

    detections = detect_wedged_listener(runners, listener_logs=listener_logs, threshold_minutes=10.0, now_ts=now_ts)
    assert len(detections) == 1
    det = detections[0]
    assert det.kind == DetectionKind.WEDGED_LISTENER
    assert det.target == "runner-wedged"
    assert det.host == "node-1"
    assert det.auto_heal_eligible is True
    assert det.recommended_action == "maintenance.runner_restart"


def test_detect_offline_runner_assigned_job() -> None:
    runners = [
        {"id": 1, "name": "runner-dead", "status": "offline", "host": "node-3"},
        {"id": 2, "name": "runner-alive", "status": "online", "host": "node-1"},
    ]
    active_jobs = [
        {
            "id": 901,
            "run_id": 501,
            "repo": "org/repo-c",
            "runner_name": "runner-dead",
            "status": "in_progress",
        },
        {
            "id": 902,
            "run_id": 502,
            "repo": "org/repo-c",
            "runner_name": "runner-alive",
            "status": "in_progress",
        },
    ]

    detections = detect_offline_runner_assigned_job(runners, active_jobs)
    assert len(detections) == 1
    det = detections[0]
    assert det.kind == DetectionKind.OFFLINE_RUNNER_ASSIGNED_JOB
    assert det.target == "org/repo-c#501"
    assert det.severity == "high"
    assert det.auto_heal_eligible is False
    assert det.recommended_action == "maintenance.run_cancel"


def test_detect_ghost_runners() -> None:
    now_ts = 1700000000.0
    runners = [
        {"name": "ghost-1", "status": "offline", "host": "decommissioned-node"},
        {"name": "healthy-1", "status": "online", "host": "live-node-1"},
    ]
    known_hosts = {"live-node-1"}
    heartbeats = {"live-node-1": now_ts - 60}

    detections = detect_ghost_runners(runners, known_hosts=known_hosts, heartbeats=heartbeats, now_ts=now_ts)
    assert len(detections) == 1
    det = detections[0]
    assert det.kind == DetectionKind.GHOST_RUNNER
    assert det.target == "ghost-1"
    assert det.severity == "medium"
    assert det.auto_heal_eligible is False
    assert det.recommended_action == "maintenance.diagnose"


def test_detect_orphaned_worktrees() -> None:
    now_ts = 1700000000.0
    worktrees = [
        {
            "path": "/wt/wt-1",
            "mtime": now_ts - (10 * 86400),
        },  # 10 days old (threshold 7)
        {"path": "/wt/wt-2", "mtime": now_ts - (2 * 86400)},  # 2 days old
    ]
    detections = detect_orphaned_worktrees(worktrees, threshold_days=7.0, now_ts=now_ts)
    assert len(detections) == 1
    det = detections[0]
    assert det.kind == DetectionKind.ORPHANED_WORKTREES
    assert det.auto_heal_eligible is True
    assert det.recommended_action == "maintenance.trim_worktrees"


def test_run_all_detectors_isolates_exceptions() -> None:
    def buggy_detector() -> list[Detection]:
        raise ValueError("Corrupted GitHub API payload")

    def ok_detector() -> list[Detection]:
        return [
            Detection(
                kind=DetectionKind.WEDGED_LISTENER,
                target="runner-x",
                host="host-1",
                severity="high",
                details={},
                recommended_action="maintenance.runner_restart",
                action_params={"runner_name": "runner-x"},
                auto_heal_eligible=True,
            )
        ]

    dets, errors = run_all_detectors([("buggy", buggy_detector), ("ok", ok_detector)])
    assert len(dets) == 1
    assert dets[0].target == "runner-x"
    assert len(errors) == 1
    assert errors[0]["detector"] == "buggy"
    assert "Corrupted GitHub API payload" in errors[0]["error"]


def test_playbook_engine_auto_heals_wedged_listener() -> None:
    c_store = get_conversation_store()
    w_store = get_work_item_store()
    engine = MaintenancePlaybookEngine(conversation_store=c_store, work_item_store=w_store)

    det = Detection(
        kind=DetectionKind.WEDGED_LISTENER,
        target="runner-wedge-1",
        host="node-alpha",
        severity="high",
        details={"reason": "stale log"},
        recommended_action="maintenance.runner_restart",
        action_params={"runner_name": "runner-wedge-1", "host": "node-alpha"},
        auto_heal_eligible=True,
    )

    with patch("staff.maintenance._run_service_command", return_value=(0, "restarted", "")):
        res = engine.remediate(det)

    assert res.executed is True
    assert res.status == "auto_healed"

    # Verify work item updated to done
    assert res.work_item_id is not None
    wi = w_store.get_work_item(res.work_item_id)
    assert wi is not None
    assert wi.state == "done"
    assert wi.owner_role == "maintenance"

    # Verify thread notification posted
    msgs = c_store.list_messages(res.thread_id)
    assert any("Self-Heal Executed" in m.body_md for m in msgs)


def test_playbook_engine_high_risk_creates_proposal_and_waits() -> None:
    c_store = get_conversation_store()
    w_store = get_work_item_store()
    engine = MaintenancePlaybookEngine(conversation_store=c_store, work_item_store=w_store)

    det = Detection(
        kind=DetectionKind.RUNNING_PAST_P95,
        target="repo-x#999",
        host="node-beta",
        severity="high",
        details={"duration": 9000},
        recommended_action="maintenance.run_cancel",
        action_params={"repo": "repo-x", "run_id": 999},
        auto_heal_eligible=False,
    )

    res = engine.remediate(det)
    assert res.executed is False
    assert res.status == "proposed"
    assert res.proposal_id is not None

    # Work item in waiting_on_user state
    wi = w_store.get_work_item(res.work_item_id)
    assert wi is not None
    assert wi.state == "waiting_on_user"

    # Proposal created in conversation store
    prop = c_store.get_proposal(res.proposal_id)
    assert prop is not None
    assert prop.action == "maintenance.run_cancel"
    assert prop.state == "proposed"


def test_auto_heal_rate_limit_escalation() -> None:
    tracker = AutoHealRateTracker(max_per_host_per_hour=3)
    c_store = get_conversation_store()
    w_store = get_work_item_store()
    engine = MaintenancePlaybookEngine(
        conversation_store=c_store,
        work_item_store=w_store,
        rate_tracker=tracker,
    )

    now = time.time()
    # 3 successful auto heals on node-1
    for i in range(3):
        tracker.record_attempt(
            host="node-1",
            target=f"runner-{i}",
            action="maintenance.runner_restart",
            success=True,
            ts=now,
        )

    # 4th auto-heal should be blocked by rate limit and escalate to proposal
    det = Detection(
        kind=DetectionKind.WEDGED_LISTENER,
        target="runner-4",
        host="node-1",
        severity="high",
        details={},
        recommended_action="maintenance.runner_restart",
        action_params={"runner_name": "runner-4", "host": "node-1"},
        auto_heal_eligible=True,
    )

    res = engine.remediate(det)
    assert res.executed is False
    assert res.status == "proposed"
    assert "hourly host self-heal budget exceeded" in res.reason

    # Proposal created with reason
    prop = c_store.get_proposal(res.proposal_id)
    assert prop is not None
    assert "hourly host self-heal budget exceeded" in prop.reason


def test_repeat_failure_escalates_to_proposal() -> None:
    tracker = AutoHealRateTracker(max_per_host_per_hour=3)
    c_store = get_conversation_store()
    w_store = get_work_item_store()
    engine = MaintenancePlaybookEngine(
        conversation_store=c_store,
        work_item_store=w_store,
        rate_tracker=tracker,
    )

    now = time.time()
    # Target failed previously
    tracker.record_attempt(
        host="node-1",
        target="runner-flaky",
        action="maintenance.runner_restart",
        success=False,
        ts=now - 100,
    )

    det = Detection(
        kind=DetectionKind.WEDGED_LISTENER,
        target="runner-flaky",
        host="node-1",
        severity="high",
        details={},
        recommended_action="maintenance.runner_restart",
        action_params={"runner_name": "runner-flaky", "host": "node-1"},
        auto_heal_eligible=True,
    )

    res = engine.remediate(det)
    assert res.executed is False
    assert res.status == "proposed"
    assert "repeat self-heal failure" in res.reason


def test_playbook_execution_failure_escalates() -> None:
    c_store = get_conversation_store()
    w_store = get_work_item_store()
    engine = MaintenancePlaybookEngine(conversation_store=c_store, work_item_store=w_store)

    det = Detection(
        kind=DetectionKind.WEDGED_LISTENER,
        target="runner-fail-service",
        host="node-alpha",
        severity="high",
        details={},
        recommended_action="maintenance.runner_restart",
        action_params={"runner_name": "runner-fail-service", "host": "node-alpha"},
        auto_heal_eligible=True,
    )

    with patch(
        "staff.maintenance._run_service_command",
        return_value=(1, "", "systemctl failed"),
    ):
        res = engine.remediate(det)

    assert res.executed is True
    assert res.status == "escalated"
    assert res.proposal_id is not None

    wi = w_store.get_work_item(res.work_item_id)
    assert wi is not None
    assert wi.state == "escalated"
