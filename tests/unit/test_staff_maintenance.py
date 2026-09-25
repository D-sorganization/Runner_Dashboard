"""Unit tests for the Staff Maintenance Action Catalogue (SC-E3, Issue #1321)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from identity import Principal
from staff.actions import (
    ACTION_REGISTRY,
    ActionContext,
    ActionResult,
    ActionRiskClass,
)
from staff.conversations import reset_conversation_store
from staff.maintenance import (
    MAX_BATCH_RUNNERS,
    MaintenanceCooldownError,
    MaintenancePreconditionError,
    execute_maintenance,
    get_maintenance_cooldown_tracker,
    reset_maintenance_cooldowns,
    verify_maintenance,
)


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_conversation_store()
    reset_maintenance_cooldowns()
    db_file = tmp_path / "staff_test.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    yield
    reset_conversation_store()
    reset_maintenance_cooldowns()


TEST_CALLER = Principal(id="dieter", name="Dieter", type="human", roles=["admin"])


def test_maintenance_actions_registered_in_action_registry() -> None:
    expected_actions = [
        "maintenance.runner_start",
        "maintenance.runner_stop",
        "maintenance.runner_restart",
        "maintenance.runner_drain",
        "maintenance.group_start",
        "maintenance.group_stop",
        "maintenance.fleet_control",
        "maintenance.queue_purge_stale",
        "maintenance.run_cancel",
        "maintenance.run_rerun",
        "maintenance.trim_worktrees",
        "maintenance.vacuum_sqlite",
        "maintenance.diagnose",
    ]
    for name in expected_actions:
        defn = ACTION_REGISTRY.get(name)
        assert defn is not None, f"Action {name} must be registered"
        assert defn.risk_class in (
            ActionRiskClass.READ,
            ActionRiskClass.LOW,
            ActionRiskClass.MEDIUM,
            ActionRiskClass.HIGH,
            ActionRiskClass.OWNER_ONLY,
        )
        assert defn.executor is not None
        assert defn.verifier is not None


def test_cooldown_enforcement_between_repeated_actions() -> None:
    ctx = ActionContext(thread_id="th1", caller=TEST_CALLER)
    params = {"runner_name": "runner-1", "host": "local"}

    # First dry run / execution succeeds and records timestamp
    tracker = get_maintenance_cooldown_tracker()
    tracker.record("maintenance.runner_restart", "runner-1")

    # Immediate second execution within cooldown period raises MaintenanceCooldownError
    with pytest.raises(MaintenanceCooldownError, match="Cooldown active"):
        execute_maintenance(
            "maintenance.runner_restart",
            params,
            ctx,
            cooldown_seconds=30.0,
        )


def test_blast_radius_limits_on_batch_actions() -> None:
    ctx = ActionContext(thread_id="th1", caller=TEST_CALLER)
    # Excessive count exceeding MAX_BATCH_RUNNERS is rejected
    params = {"group_label": "gpu-workers", "max_count": MAX_BATCH_RUNNERS + 5}
    with pytest.raises(MaintenancePreconditionError, match="exceeds safety blast-radius"):
        execute_maintenance("maintenance.group_start", params, ctx)


def test_fleet_control_rejects_all_hosts_for_disruptive_action() -> None:
    ctx = ActionContext(thread_id="th1", caller=TEST_CALLER)
    # Attempting to stop or restart 'all' hosts simultaneously is blocked
    params = {"action": "down", "host": "all"}
    with pytest.raises(MaintenancePreconditionError, match="One host at a time"):
        execute_maintenance("maintenance.fleet_control", params, ctx)


def test_runner_stop_precondition_requires_drain_when_busy() -> None:
    ctx = ActionContext(thread_id="th1", caller=TEST_CALLER)
    params = {"runner_name": "runner-busy", "host": "local", "drain": False, "force": False}

    with patch("staff.maintenance._get_runner_state", return_value={"busy": True, "status": "online"}):
        with pytest.raises(MaintenancePreconditionError, match="is busy; drain first"):
            execute_maintenance("maintenance.runner_stop", params, ctx)


def test_maintenance_dry_run_reports_exact_effect_without_side_effects() -> None:
    ctx = ActionContext(thread_id="th1", caller=TEST_CALLER, dry_run=True)
    params = {"runner_name": "runner-2", "host": "local"}

    with patch("staff.maintenance._run_service_command") as mock_svc:
        res = execute_maintenance("maintenance.runner_restart", params, ctx)
        assert res.success is True
        assert res.result["dry_run"] is True
        assert "planned_steps" in res.result
        # Ensure no actual mutating subprocess command was invoked
        mock_svc.assert_not_called()


def test_runner_stop_with_drain_succeeds() -> None:
    ctx = ActionContext(thread_id="th1", caller=TEST_CALLER)
    params = {"runner_name": "runner-3", "host": "local", "drain": True}

    with (
        patch("staff.maintenance._get_runner_state", return_value={"busy": True, "status": "online"}),
        patch("staff.maintenance._drain_runner", return_value=True) as mock_drain,
        patch("staff.maintenance._run_service_command", return_value=(0, "stopped", "")) as mock_svc,
    ):
        res = execute_maintenance("maintenance.runner_stop", params, ctx)
        assert res.success is True
        mock_drain.assert_called_once_with("runner-3", "local")
        mock_svc.assert_called_once()


def test_verification_failure_marks_action_failed() -> None:
    ctx = ActionContext(thread_id="th1", caller=TEST_CALLER)
    params = {"runner_name": "runner-stubborn", "host": "local"}
    action_res = ActionResult(success=True, result={"target": "runner-stubborn", "action": "stop"})

    # Verifier checks state, but runner is still reporting 'online' instead of 'stopped'
    with patch("staff.maintenance._get_runner_state", return_value={"busy": False, "status": "online"}):
        ok, msg = verify_maintenance(action_res, params, ctx, action_name="maintenance.runner_stop")
        assert ok is False
        assert "expected stopped" in msg.lower()


def test_partial_failure_across_targets_reports_failure() -> None:
    ctx = ActionContext(thread_id="th1", caller=TEST_CALLER)
    params = {"group_label": "ci-workers", "max_count": 5}

    runners = [
        {"id": 1, "name": "runner-a", "status": "online"},
        {"id": 2, "name": "runner-b", "status": "online"},
    ]

    def _fake_run_svc(target: str, cmd: str, host: str = "local") -> tuple[int, str, str]:
        if target == "runner-b":
            return (1, "", "Service stop failed: timeout")
        return (0, "stopped", "")

    with (
        patch("staff.maintenance._get_group_runners", return_value=runners),
        patch("staff.maintenance._run_service_command", side_effect=_fake_run_svc),
    ):
        res = execute_maintenance("maintenance.group_stop", params, ctx)
        assert res.success is False
        assert res.failure_class == "partial_failure"
        per_target = res.result["per_target"]
        assert per_target["runner-a"]["success"] is True
        assert per_target["runner-b"]["success"] is False


def test_vacuum_sqlite_and_trim_worktrees_execution() -> None:
    ctx = ActionContext(thread_id="th1", caller=TEST_CALLER)
    with (
        patch("staff.maintenance._vacuum_db", return_value={"freed_bytes": 10240, "database": "staff_runs.sqlite3"}),
        patch("staff.maintenance._trim_worktrees_fs", return_value={"trimmed_count": 2, "pruned": ["wt1", "wt2"]}),
    ):
        res_vac = execute_maintenance("maintenance.vacuum_sqlite", {"database": "staff_runs.sqlite3"}, ctx)
        assert res_vac.success is True
        assert res_vac.result["freed_bytes"] == 10240

        res_trim = execute_maintenance("maintenance.trim_worktrees", {}, ctx)
        assert res_trim.success is True
        assert res_trim.result["trimmed_count"] == 2
