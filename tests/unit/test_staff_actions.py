"""Unit tests for staff action registry, approval policies, execution, and replay (SC-B6, Issue #1313)."""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
import pytest
from identity import Principal
from staff.actions import (
    ACTION_REGISTRY,
    ActionContext,
    ActionDefinition,
    ActionResult,
    ActionRiskClass,
    ProposalExpiredError,
    ProposalReplayError,
    check_approval_policy,
    check_role_permission,
    execute_proposal,
    is_proposal_expired,
)
from staff.audit import reset_audit_store
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.roles import RoleSpec

UTC = getattr(_dt, "UTC", _dt.UTC)
datetime = _dt.datetime


TEST_USER = Principal(
    id="test-user",
    type="human",
    name="Test User",
    roles=["developer"],
    scopes=["staff.read", "staff.chat"],
)

TEST_APPROVER = Principal(
    id="test-approver",
    type="human",
    name="Test Approver",
    roles=["operator"],
    scopes=["staff.read", "staff.approve"],
)

TEST_OWNER = Principal(
    id="test-owner",
    type="human",
    name="Test Owner",
    roles=["owner", "admin"],
    scopes=["staff.read", "staff.approve", "admin", "owner"],
)


@pytest.fixture(autouse=True)
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_file = tmp_path / "staff_actions_test.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_file))
    reset_conversation_store()
    reset_audit_store()
    yield
    reset_conversation_store()
    reset_audit_store()


def test_action_registry_contains_initial_actions() -> None:
    actions = ACTION_REGISTRY.list_actions()
    names = {a.name for a in actions}
    expected = {
        "staff.dispatch",
        "staff.review_pr",
        "staff.hold",
        "staff.unhold",
        "code_request.create",
        "board.propose",
        "maintenance.runner_restart",
        "maintenance.runner_stop",
        "maintenance.diagnose",
    }
    assert expected.issubset(names), f"Missing actions: {expected - names}"

    dispatch_act = ACTION_REGISTRY.get("staff.dispatch")
    assert dispatch_act is not None
    assert dispatch_act.risk_class == ActionRiskClass.MEDIUM
    assert dispatch_act.required_scope == "staff.dispatch"

    hold_act = ACTION_REGISTRY.get("staff.hold")
    assert hold_act is not None
    assert hold_act.risk_class == ActionRiskClass.HIGH
    assert hold_act.required_scope == "staff.holds.write"


def test_approval_policy_matrix() -> None:
    store = get_conversation_store()
    th = store.create_thread(title="Test Policy", kind="direct", participants=["barb", "user"])
    prop_low = store.create_proposal(
        message_id="msg_1",
        thread_id=th.id,
        action="staff.review_pr",
        params={"repo": "Tools", "pr": 10},
        risk="low",
        principal="barb",
    )
    prop_medium = store.create_proposal(
        message_id="msg_2",
        thread_id=th.id,
        action="staff.dispatch",
        params={"role": "librarian", "repo": "Tools"},
        risk="medium",
        principal="barb",
    )
    prop_high = store.create_proposal(
        message_id="msg_3",
        thread_id=th.id,
        action="staff.hold",
        params={"text": "no deployments", "applies_to": ["*"]},
        risk="high",
        principal="barb",
    )

    # Low risk can be decided by approver
    check_approval_policy(ACTION_REGISTRY.get("staff.review_pr"), prop_low, TEST_APPROVER)

    # Medium risk needs staff.approve scope
    with pytest.raises(PermissionError, match="staff.approve"):
        check_approval_policy(ACTION_REGISTRY.get("staff.dispatch"), prop_medium, TEST_USER)
    check_approval_policy(ACTION_REGISTRY.get("staff.dispatch"), prop_medium, TEST_APPROVER)

    # High risk requires owner
    with pytest.raises(PermissionError, match="owner approval"):
        check_approval_policy(ACTION_REGISTRY.get("staff.hold"), prop_high, TEST_APPROVER)
    check_approval_policy(ACTION_REGISTRY.get("staff.hold"), prop_high, TEST_OWNER)


def test_permission_denial_role_not_permitted() -> None:
    # A role without permission cannot get an action executed even if proposed
    unauthorized_role = RoleSpec(
        name="intern_bot",
        title="Intern Bot",
        permissions={"allowed_actions": ["maintenance.diagnose"]},
    )
    authorized_role = RoleSpec(
        name="lead_dev",
        title="Lead Dev",
        permissions={"allowed_actions": ["staff.dispatch", "staff.review_pr"]},
    )

    dispatch_act = ACTION_REGISTRY.get("staff.dispatch")
    assert not check_role_permission(dispatch_act, "intern_bot", role_spec=unauthorized_role)
    assert check_role_permission(dispatch_act, "lead_dev", role_spec=authorized_role)

    # Barb has default permission for standard staff actions
    assert check_role_permission(dispatch_act, "barb", role_spec=None)

    # Maintenance role has maintenance.* actions
    maint_role = RoleSpec(
        name="maintenance",
        title="Maintenance Role",
        permissions={"fleet_actions": ["maintenance.*", "runner.restart"]},
    )
    restart_act = ACTION_REGISTRY.get("maintenance.runner_restart")
    assert check_role_permission(restart_act, "maintenance", role_spec=maint_role)
    assert not check_role_permission(restart_act, "intern_bot", role_spec=unauthorized_role)


def test_proposal_expiry_24h() -> None:
    store = get_conversation_store()
    th = store.create_thread(title="Test Expiry", kind="direct", participants=["barb", "user"])

    prop = store.create_proposal(
        message_id="msg_exp",
        thread_id=th.id,
        action="staff.dispatch",
        params={"role": "librarian", "repo": "Tools"},
        risk="medium",
        principal="barb",
    )

    # Not expired initially
    assert not is_proposal_expired(prop)

    # Manipulate created_at to 25 hours ago
    old_time = (datetime.now(UTC) - _dt.timedelta(hours=25)).isoformat()
    with store._lock:
        store._conn.execute("UPDATE action_proposals SET created_at = ? WHERE id = ?", (old_time, prop.id))

    expired_prop = store.get_proposal(prop.id)
    assert expired_prop is not None
    assert is_proposal_expired(expired_prop)

    with pytest.raises(ProposalExpiredError, match="expired"):
        execute_proposal(prop.id, approver=TEST_APPROVER, store=store, approve=True)

    refreshed = store.get_proposal(prop.id)
    assert refreshed is not None
    assert refreshed.state == "expired"


def test_proposal_replay_protection() -> None:
    store = get_conversation_store()
    th = store.create_thread(title="Test Replay", kind="direct", participants=["barb", "user"])

    prop = store.create_proposal(
        message_id="msg_rep",
        thread_id=th.id,
        action="maintenance.diagnose",
        params={},
        risk="low",
        principal="barb",
    )

    # Deny proposal
    store.decide_proposal(prop.id, state="denied", decided_by="operator", reason="declined")

    # Cannot execute denied proposal
    with pytest.raises(ProposalReplayError, match="denied"):
        execute_proposal(prop.id, approver=TEST_APPROVER, store=store, approve=True)

    # Expired proposal cannot be executed
    prop2 = store.create_proposal(
        message_id="msg_rep2",
        thread_id=th.id,
        action="maintenance.diagnose",
        params={},
        risk="low",
        principal="barb",
    )
    store.transition_proposal_state(prop2.id, "expired", reason="expired")
    with pytest.raises(ProposalReplayError, match="expired"):
        execute_proposal(prop2.id, approver=TEST_APPROVER, store=store, approve=True)


def test_execute_staff_dispatch_success_and_thread_messages() -> None:
    store = get_conversation_store()
    th = store.create_thread(title="Test Dispatch", kind="direct", participants=["barb", "user"])

    prop = store.create_proposal(
        message_id="msg_disp",
        thread_id=th.id,
        action="staff.dispatch",
        params={"role": "ad-hoc", "repo": "Repository_Management", "prompt": "check catalog"},
        risk="medium",
        principal="barb",
    )

    async def off_loop() -> ActionResult:  # as the proposal routes run it (#1448, #1487)
        return await anyio.to_thread.run_sync(
            partial(execute_proposal, prop.id, approver=TEST_APPROVER, store=store, approve=True)
        )

    result = anyio.run(off_loop)
    assert result.success is True, result.error
    assert result.run_id is not None
    assert result.run_id.startswith("run-") or result.run_id.startswith("run_")

    # Check proposal state transitioned to done
    updated_prop = store.get_proposal(prop.id)
    assert updated_prop is not None
    assert updated_prop.state == "done"

    # Check thread messages contain action_result and run_card
    msgs = store.list_messages(th.id)
    kinds = [m.kind for m in msgs]
    assert "action_result" in kinds
    assert "run_card" in kinds

    # Verify run_card carries run_id
    run_card_msg = next(m for m in msgs if m.kind == "run_card")
    assert run_card_msg.run_id == result.run_id


def test_executor_failure_marks_proposal_failed_and_permits_retry() -> None:
    store = get_conversation_store()
    th = store.create_thread(title="Test Fail", kind="direct", participants=["barb", "user"])

    # Register temporary failing action
    def _fail_executor(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
        if params.get("should_fail"):
            return ActionResult(
                success=False,
                error="Upstream runner service unreachable",
                failure_class="service_unavailable",
            )
        return ActionResult(success=True, result="Recovered")

    failing_action = ActionDefinition(
        name="test.fail_then_retry",
        description="Action that can fail and retry",
        risk_class=ActionRiskClass.LOW,
        required_scope="staff.read",
        executor=_fail_executor,
    )
    ACTION_REGISTRY.register(failing_action)

    prop = store.create_proposal(
        message_id="msg_f",
        thread_id=th.id,
        action="test.fail_then_retry",
        params={"should_fail": True},
        risk="low",
        principal="barb",
    )

    result1 = execute_proposal(prop.id, approver=TEST_APPROVER, store=store, approve=True)
    assert result1.success is False
    assert result1.failure_class == "service_unavailable"

    updated = store.get_proposal(prop.id)
    assert updated is not None
    assert updated.state == "failed"

    # Retry with failing condition removed
    updated.params["should_fail"] = False
    with store._lock:
        import json

        store._conn.execute(
            "UPDATE action_proposals SET params = ? WHERE id = ?",
            (json.dumps(updated.params), prop.id),
        )

    # A failed proposal only runs again after an explicit retry decision (#1485).
    store.decide_proposal(prop.id, "approved", decided_by="operator", reason="retry")
    result2 = execute_proposal(prop.id, approver=TEST_APPROVER, store=store)
    assert result2.success is True
    updated2 = store.get_proposal(prop.id)
    assert updated2 is not None
    assert updated2.state == "done"


def test_verifier_mismatch_fails_action() -> None:
    store = get_conversation_store()
    th = store.create_thread(title="Test Verifier", kind="direct", participants=["barb", "user"])

    def _good_exec(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
        return ActionResult(success=True, result="Done")

    def _failing_verifier(res: ActionResult, params: dict[str, Any], ctx: ActionContext) -> tuple[bool, str]:
        return False, "Target state did not match expected hash"

    mismatch_action = ActionDefinition(
        name="test.verifier_mismatch",
        description="Action whose verifier reports mismatch",
        risk_class=ActionRiskClass.LOW,
        required_scope="staff.read",
        executor=_good_exec,
        verifier=_failing_verifier,
    )
    ACTION_REGISTRY.register(mismatch_action)

    prop = store.create_proposal(
        message_id="msg_v",
        thread_id=th.id,
        action="test.verifier_mismatch",
        params={},
        risk="low",
        principal="barb",
    )

    res = execute_proposal(prop.id, approver=TEST_APPROVER, store=store, approve=True)
    assert res.success is False
    assert res.verification_ok is False
    assert "mismatch" in (res.error or "").lower()

    updated = store.get_proposal(prop.id)
    assert updated is not None
    assert updated.state == "failed"


def test_staff_hold_and_unhold_lifecycle() -> None:
    store = get_conversation_store()
    th = store.create_thread(title="Test Holds", kind="direct", participants=["barb", "user"])

    hold_prop = store.create_proposal(
        message_id="msg_h1",
        thread_id=th.id,
        action="staff.hold",
        params={"text": "Emergency freeze on deployments", "applies_to": ["*"]},
        risk="high",
        principal="barb",
    )

    res_hold = execute_proposal(hold_prop.id, approver=TEST_OWNER, store=store, approve=True)
    assert res_hold.success is True
    assert res_hold.verification_ok is True

    # Unhold proposal
    unhold_prop = store.create_proposal(
        message_id="msg_h2",
        thread_id=th.id,
        action="staff.unhold",
        params={"text": "Emergency freeze on deployments"},
        risk="high",
        principal="barb",
    )

    res_unhold = execute_proposal(unhold_prop.id, approver=TEST_OWNER, store=store, approve=True)
    assert res_unhold.success is True
    assert res_unhold.verification_ok is True
