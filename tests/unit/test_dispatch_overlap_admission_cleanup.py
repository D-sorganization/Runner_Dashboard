"""Dispatch overlap session cleanup on staff dispatch exits (#4272 / #4294 review)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from dispatch_routing import dispatch_queue_manager
from fastapi import HTTPException
from identity import Principal
from staff import claim_guard, dispatch_service
from staff.audit import AuditError
from staff.dispatch_service import DispatchCommand, dispatch_staff_run
from staff.plan import RunPlan

CALLER = Principal(id="operator-user", type="human", name="Operator", roles=["operator"], scopes=["staff.dispatch"])


@pytest.mark.asyncio
async def test_staff_dispatch_releases_overlap_session_when_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overlap sessions must not leak when dispatch exits after path admission."""
    dispatch_queue_manager.reset_state_for_tests()
    premise = type("P", (), {"allowed": True, "reason": "", "detail": "", "comment": None, "should_close": False})()

    async def allow_premise(*_a: Any, **_k: Any) -> Any:
        return premise

    def audit_down(**_kwargs: Any) -> int:
        raise AuditError("audit database is locked")

    monkeypatch.setattr("dispatch_premise.evaluate_dispatch_premise", allow_premise)
    monkeypatch.setattr("staff.preflight_duplicate_guard.check_duplicate_pr_or_run", AsyncMock())
    monkeypatch.setattr(claim_guard, "issue_attempt_blocker", lambda *a, **k: None)
    monkeypatch.setattr(claim_guard, "enforce_claim_guard", AsyncMock(return_value={"claim_check": "clear"}))
    monkeypatch.setattr(dispatch_service, "record_audit", audit_down)
    fake_plan = RunPlan(
        role="ad-hoc",
        provider="claude",
        model=None,
        repo="Runner_Dashboard",
        target_kind="issue",
        target_ref="#9",
        operator_prompt="",
        prompt="fix `backend/widget.py`",
        argv=["claude"],
        branch="staff/9",
        lease_ritual=False,
    )
    monkeypatch.setattr("staff.runner.StaffRunner.plan", lambda _self, _req: fake_plan)
    monkeypatch.setattr(
        "staff.provider_dispatch_preflight.refusal_for_provider_auth",
        lambda *_a, **_k: None,
    )

    cmd = DispatchCommand(
        role="ad-hoc",
        repo="Runner_Dashboard",
        issue=9,
        prompt="fix `backend/widget.py`",
        requested_by="operator-user",
        provider="claude",
    )
    with pytest.raises(HTTPException) as err:
        await dispatch_staff_run(cmd, CALLER)
    assert err.value.status_code == 503
    assert dispatch_queue_manager.get_active_sessions("Runner_Dashboard") == []
