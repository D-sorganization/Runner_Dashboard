"""Direct tests for the staff.hold / staff.unhold executors and verifiers (#1313, #1726).

``test_staff_actions`` covers the happy lifecycle through ``execute_proposal``; these pin the
executors' own edge cases, including re-holding text that already exists as a role guardrail.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from staff.actions import ActionContext, ActionResult
from staff.audit import StaffAuditStore
from staff.hold_actions import execute_staff_hold, execute_staff_unhold, verify_staff_hold, verify_staff_unhold
from staff.holds import Hold, HoldsList

pytestmark = pytest.mark.unit


@pytest.fixture
def holds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HoldsList:
    monkeypatch.setenv("STAFF_HOLDS_FILE", str(tmp_path / "holds.json"))
    hl = HoldsList(roles_loader=dict)
    hl.replace([])
    return hl


@pytest.fixture
def ctx(tmp_path: Path) -> ActionContext:
    return ActionContext(thread_id="th-1", audit_store=StaffAuditStore(tmp_path / "audit.sqlite3"))


def _seed(holds: HoldsList, *items: Hold) -> None:
    holds.replace([h.to_dict() for h in items])


def _audit_actions(ctx: ActionContext) -> list[str]:
    assert ctx.audit_store is not None
    return [e.action for e in ctx.audit_store.list_entries()]


def test_hold_requires_text(holds: HoldsList, ctx: ActionContext) -> None:
    res = execute_staff_hold({"text": "   "}, ctx)
    assert (res.success, res.failure_class) == (False, "invalid_params")
    assert holds.load() == []


def test_new_hold_blocks_scheduling_and_is_audited(holds: HoldsList, ctx: ActionContext) -> None:
    res = execute_staff_hold({"text": "Pause deploys", "applies_to": "repo:RD"}, ctx)
    assert res.success is True
    blocking = holds.blocking("any-role", repo="RD")
    assert blocking is not None and blocking.id == res.result["hold_id"]
    assert blocking.applies_to == ["repo:RD"]
    assert _audit_actions(ctx) == ["hold_set"]


def test_rehold_of_a_guardrail_makes_it_block_scheduling(holds: HoldsList, ctx: ActionContext) -> None:
    _seed(holds, Hold(id="hold-g", text="No deploys", applies_to=["barb"], kind="guardrail"))
    res = execute_staff_hold({"text": "no deploys", "applies_to": ["*"]}, ctx)
    assert res.success is True
    blocking = holds.blocking("any-role")
    assert blocking is not None and blocking.id == "hold-g"
    assert len(holds.load()) == 1


def test_rehold_reactivates_and_merges_targets_in_stable_order(holds: HoldsList, ctx: ActionContext) -> None:
    _seed(holds, Hold(id="hold-x", text="Freeze", applies_to=["zeta", "alpha"], active=False))
    execute_staff_hold({"text": "Freeze", "applies_to": ["beta", "alpha"]}, ctx)
    (hold,) = holds.load()
    assert hold.active is True
    assert hold.applies_to == ["alpha", "beta", "zeta"]


def test_verify_hold_reports_inactive(holds: HoldsList, ctx: ActionContext) -> None:
    _seed(holds, Hold(id="hold-x", text="Freeze", applies_to=["*"], active=False))
    res = ActionResult(success=True, result={"hold_id": "hold-x"})
    assert verify_staff_hold(res, {"text": "Freeze"}, ctx) == (False, "Hold not found or inactive")


def test_verify_hold_rejects_a_guardrail_that_does_not_block(holds: HoldsList, ctx: ActionContext) -> None:
    _seed(holds, Hold(id="hold-g", text="No deploys", applies_to=["*"], kind="guardrail"))
    res = ActionResult(success=True, result={"hold_id": "hold-g"})
    ok, _ = verify_staff_hold(res, {"text": "No deploys"}, ctx)
    assert ok is False


@pytest.mark.parametrize("params", [{}, {"hold_id": " ", "text": ""}])
def test_unhold_requires_id_or_text(holds: HoldsList, ctx: ActionContext, params: dict[str, Any]) -> None:
    res = execute_staff_unhold(params, ctx)
    assert (res.success, res.failure_class) == (False, "invalid_params")


def test_unhold_unknown_hold_is_not_found_and_not_audited(holds: HoldsList, ctx: ActionContext) -> None:
    res = execute_staff_unhold({"hold_id": "hold-missing"}, ctx)
    assert (res.success, res.failure_class) == (False, "not_found")
    assert _audit_actions(ctx) == []


def test_unhold_by_text_is_case_insensitive(holds: HoldsList, ctx: ActionContext) -> None:
    _seed(holds, Hold(id="hold-x", text="Freeze Deploys", applies_to=["*"]))
    res = execute_staff_unhold({"text": "FREEZE deploys"}, ctx)
    assert res.success is True
    assert holds.blocking("any-role") is None
    assert verify_staff_unhold(res, {"text": "freeze deploys"}, ctx) == (True, "Hold verified lifted")
    assert _audit_actions(ctx) == ["hold_lift"]


def test_verify_unhold_reports_a_still_active_hold(holds: HoldsList, ctx: ActionContext) -> None:
    _seed(holds, Hold(id="hold-x", text="Freeze", applies_to=["*"]))
    res = ActionResult(success=True, result={"hold_id": "hold-x", "lifted": True})
    assert verify_staff_unhold(res, {"hold_id": "hold-x"}, ctx) == (False, "Hold still active")
