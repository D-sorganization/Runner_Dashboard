"""Unit tests for staff action registry, approval policies, execution, and replay (SC-B6, Issue #1313)."""

from __future__ import annotations

import ast
import datetime as _dt
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
import pytest
from code_requests.lifecycle import CodeRequestState
from code_requests.model import CodeRequest, Requester, RequesterKind
from code_requests.store import get_code_request_store, reset_code_request_store
from identity import Principal
from staff.action_executors import BOARD_PROPOSAL_ROLE
from staff.actions import (
    ACTION_REGISTRY,
    ActionContext,
    ActionDefinition,
    ActionResult,
    ActionRiskClass,
    ProposalExpiredError,
    ProposalReplayError,
    RolePermissionDeniedError,
    check_approval_policy,
    check_role_permission,
    execute_proposal,
    is_proposal_expired,
)
from staff.audit import reset_audit_store
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.roles import RoleSpec
from staff.runner import StaffRunner, reset_runner
from staff.store import reset_store

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
    monkeypatch.setattr(StaffRunner, "_worker", lambda *args, **kwargs: None)
    get_code_request_store(cache_path=tmp_path / "code_requests.json")
    reset_runner()
    reset_store()
    reset_conversation_store()
    reset_audit_store()
    yield
    reset_runner()
    reset_store()
    reset_conversation_store()
    reset_audit_store()
    reset_code_request_store()


def test_action_registry_contains_initial_actions() -> None:
    actions = ACTION_REGISTRY.list_actions()
    names = {a.name for a in actions}
    expected = {
        "staff.dispatch",
        "staff.review_pr",
        "staff.hold",
        "staff.unhold",
        "code_request.create",
        "code_request.update",
        "board.propose",
        "maintenance.runner_restart",
        "maintenance.runner_stop",
        "maintenance.diagnose",
    }
    assert expected.issubset(names), f"Missing actions: {expected - names}"

    update_act = ACTION_REGISTRY.get("code_request.update")
    assert update_act is not None
    assert update_act.risk_class == ActionRiskClass.MEDIUM
    assert update_act.required_scope == "code_requests.write"

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


def test_check_role_permission_board_proposal_role_and_dropped_alias() -> None:
    """BOARD_PROPOSAL_ROLE has default permission; underscore alias 'board_secretary' is dropped."""
    board_act = ACTION_REGISTRY.get("board.propose")
    dispatch_act = ACTION_REGISTRY.get("staff.dispatch")
    submit_act = ActionDefinition(
        name="submit_proposal",
        description="Submit a proposal",
        risk_class=ActionRiskClass.MEDIUM,
    )

    # BOARD_PROPOSAL_ROLE ('board-secretary') is authorized
    assert check_role_permission(board_act, BOARD_PROPOSAL_ROLE)
    assert check_role_permission(dispatch_act, BOARD_PROPOSAL_ROLE)
    assert check_role_permission(submit_act, BOARD_PROPOSAL_ROLE)

    # Underscore alias 'board_secretary' must be rejected
    assert not check_role_permission(board_act, "board_secretary")
    assert not check_role_permission(dispatch_act, "board_secretary")
    assert not check_role_permission(submit_act, "board_secretary")

    # reports_to check: BOARD_PROPOSAL_ROLE is accepted, 'board_secretary' is rejected
    role_reporting_canonical = RoleSpec(
        name="delegate_canonical",
        title="Delegate Canonical",
        reports_to=BOARD_PROPOSAL_ROLE,
    )
    role_reporting_alias = RoleSpec(
        name="delegate_alias",
        title="Delegate Alias",
        reports_to="board_secretary",
    )
    assert check_role_permission(board_act, "delegate_canonical", role_spec=role_reporting_canonical)
    assert not check_role_permission(board_act, "delegate_alias", role_spec=role_reporting_alias)


def test_no_board_secretary_literals_in_staff_actions() -> None:
    """staff/actions.py must not contain 'board_secretary' or 'board-secretary' literals."""
    actions_path = Path(__file__).resolve().parents[2] / "backend" / "staff" / "actions.py"
    with open(actions_path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=str(actions_path))

    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value in ("board_secretary", "board-secretary")
    ]
    assert literals == [], f"Found forbidden literals in staff/actions.py: {literals}"


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

    from staff.holds import HoldsList  # noqa: PLC0415

    created = next(h for h in HoldsList().load() if h.id == res_hold.result["hold_id"])
    assert created.kind == "schedule"  # a staff.hold action always blocks scheduling (#1726)

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


def _in_worker(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run ``fn`` in an anyio worker thread, where executors reach the loop bridge (as in production)."""
    return anyio.run(partial(anyio.to_thread.run_sync, partial(fn, *args, **kwargs)))


def _make_code_request(
    req_id: str,
    state: CodeRequestState = CodeRequestState.DRAFT,
    prompt: str = "Initial description",
) -> CodeRequest:
    now = datetime.now(UTC).isoformat()
    return CodeRequest(
        id=req_id,
        repository="Runner_Dashboard",
        title="Test Code Request",
        state=state,
        prompt=prompt,
        requester=Requester(id="test-user", kind=RequesterKind.HUMAN),
        created_at=now,
        updated_at=now,
    )


def test_code_request_update_draft_and_triage() -> None:
    cr_store = get_code_request_store()
    draft_req = _make_code_request("cr-up-draft", state=CodeRequestState.DRAFT, prompt="Draft PRD")
    triage_req = _make_code_request("cr-up-triage", state=CodeRequestState.TRIAGE, prompt="Triage PRD")
    cr_store._write_cache([draft_req, triage_req])

    store = get_conversation_store()
    th = store.create_thread(title="Test Update", kind="direct", participants=["barb", "user"])

    prop_draft = store.create_proposal(
        message_id="msg_draft",
        thread_id=th.id,
        action="code_request.update",
        params={"id": "cr-up-draft", "description": "Updated PRD draft"},
        risk="medium",
        principal="barb",
    )
    res_draft = _in_worker(execute_proposal, prop_draft.id, approver=TEST_OWNER, store=store, approve=True)
    assert res_draft.success is True, res_draft.error
    assert res_draft.result["id"] == "cr-up-draft"
    assert res_draft.result["prompt"] == "Updated PRD draft"

    cached = cr_store._read_cache()
    matching_draft = next(c for c in cached if c["id"] == "cr-up-draft")
    assert matching_draft["prompt"] == "Updated PRD draft"

    prop_triage = store.create_proposal(
        message_id="msg_triage",
        thread_id=th.id,
        action="code_request.update",
        params={"id": "cr-up-triage", "description": "Updated PRD triage"},
        risk="medium",
        principal="barb",
    )
    res_triage = _in_worker(execute_proposal, prop_triage.id, approver=TEST_OWNER, store=store, approve=True)
    assert res_triage.success is True, res_triage.error
    assert res_triage.result["prompt"] == "Updated PRD triage"


def test_code_request_update_rejects_extra_field() -> None:
    cr_store = get_code_request_store()
    req = _make_code_request("cr-up-extra", state=CodeRequestState.DRAFT)
    cr_store._write_cache([req])

    store = get_conversation_store()
    th = store.create_thread(title="Test Extra", kind="direct", participants=["barb", "user"])
    prop = store.create_proposal(
        message_id="msg_extra",
        thread_id=th.id,
        action="code_request.update",
        params={"id": "cr-up-extra", "description": "New PRD", "title": "disallowed"},
        risk="medium",
        principal="barb",
    )
    res = _in_worker(execute_proposal, prop.id, approver=TEST_OWNER, store=store, approve=True)
    assert res.success is False
    assert res.failure_class == "invalid_params"


def test_code_request_update_rejects_non_draft_triage_state() -> None:
    cr_store = get_code_request_store()
    req = _make_code_request("cr-up-plan", state=CodeRequestState.PLANNING)
    cr_store._write_cache([req])

    store = get_conversation_store()
    th = store.create_thread(title="Test Non Draft", kind="direct", participants=["barb", "user"])
    prop = store.create_proposal(
        message_id="msg_plan",
        thread_id=th.id,
        action="code_request.update",
        params={"id": "cr-up-plan", "description": "New PRD"},
        risk="medium",
        principal="barb",
    )
    res = _in_worker(execute_proposal, prop.id, approver=TEST_OWNER, store=store, approve=True)
    assert res.success is False
    assert res.failure_class == "invalid_state"
    assert "planning" in (res.error or "").lower()


def test_code_request_update_rejects_unknown_id() -> None:
    store = get_conversation_store()
    th = store.create_thread(title="Test Unknown", kind="direct", participants=["barb", "user"])
    prop = store.create_proposal(
        message_id="msg_unk",
        thread_id=th.id,
        action="code_request.update",
        params={"id": "cr-nonexistent-404", "description": "New PRD"},
        risk="medium",
        principal="barb",
    )
    res = _in_worker(execute_proposal, prop.id, approver=TEST_OWNER, store=store, approve=True)
    assert res.success is False
    assert res.failure_class == "not_found"
    assert "cr-nonexistent-404" in (res.error or "")


def test_code_request_update_role_permission_denial() -> None:
    unauthorized_role = RoleSpec(
        name="intern_bot",
        title="Intern Bot",
        permissions={"allowed_actions": ["maintenance.diagnose"]},
    )
    product_owner_role = RoleSpec(
        name="product-owner",
        title="Product Owner",
        permissions={"allowed_actions": ["code_request.update"]},
    )

    update_act = ACTION_REGISTRY.get("code_request.update")
    assert not check_role_permission(update_act, "intern_bot", role_spec=unauthorized_role)
    assert check_role_permission(update_act, "product-owner", role_spec=product_owner_role)

    store = get_conversation_store()
    th = store.create_thread(title="Test Role Denial", kind="direct", participants=["intern_bot", "user"])
    prop = store.create_proposal(
        message_id="msg_denied",
        thread_id=th.id,
        action="code_request.update",
        params={"id": "cr-1", "description": "New PRD", "proposing_role": "intern_bot"},
        risk="medium",
        principal="intern_bot",
    )
    with pytest.raises(RolePermissionDeniedError, match="not permitted"):
        execute_proposal(prop.id, approver=TEST_OWNER, store=store, approve=True)


def test_code_request_update_outside_a_worker_thread_is_bridge_unavailable() -> None:
    """Without the loop bridge the action fails cleanly instead of spinning up its own loop (#1604)."""
    from staff.action_executors import execute_code_request_update
    from staff.actions import ActionContext

    res = execute_code_request_update({"id": "cr-x", "description": "d"}, ActionContext(caller=None, thread_id="th"))
    assert res.success is False
    assert res.failure_class == "bridge_unavailable"


def test_board_propose_persists_proposal_text_on_work_item(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """execute_board_propose must not drop params['proposal'] (#1758)."""
    from staff.action_executors import execute_board_propose
    from staff.actions import ActionContext
    from staff.work_items import get_work_item_store, reset_work_item_store

    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "wi_test.sqlite3"))
    reset_work_item_store()
    proposal_text = "The Board deliberation position on the DC return electrode epic."
    res = execute_board_propose(
        {"title": "Adopt sector solver", "proposal": proposal_text},
        ActionContext(caller=None, thread_id="th-board"),
    )
    assert res.success is True
    assert res.result is not None
    assert res.result["proposal"] == proposal_text

    wi_store = get_work_item_store()
    wi = wi_store.get_work_item(res.result["proposal_id"])
    assert wi is not None
    assert wi.description == proposal_text
    reset_work_item_store()


def test_board_propose_missing_proposal_is_invalid_params() -> None:
    from staff.action_executors import execute_board_propose
    from staff.actions import ActionContext

    res = execute_board_propose({"title": "Adopt sector solver"}, ActionContext(caller=None, thread_id="th"))
    assert res.success is False
    assert res.failure_class == "invalid_params"

    res_no_title = execute_board_propose({"proposal": "text"}, ActionContext(caller=None, thread_id="th"))
    assert res_no_title.success is False
    assert res_no_title.failure_class == "invalid_params"


def test_open_pr_is_not_implemented_and_does_not_claim_success() -> None:
    """execute_open_pr must not report opened=True without actually opening a PR (#1758)."""
    from staff.action_executors import execute_open_pr
    from staff.actions import ActionContext

    res = execute_open_pr(
        {"repo": "Runner_Dashboard", "branch": "fix/example", "title": "Example"},
        ActionContext(caller=None, thread_id="th"),
    )
    assert res.success is False
    assert res.failure_class == "not_implemented"
    assert res.result is None or "opened" not in res.result


def test_open_pr_missing_params_is_invalid_params() -> None:
    from staff.action_executors import execute_open_pr
    from staff.actions import ActionContext

    res = execute_open_pr({"repo": "Runner_Dashboard"}, ActionContext(caller=None, thread_id="th"))
    assert res.success is False
    assert res.failure_class == "invalid_params"
