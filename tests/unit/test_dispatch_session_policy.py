"""Dispatch overlap sessions: read-only exemption, idle expiry, admin hooks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from dispatch_queue_link import (
    bind_run_session,
    release_run_session,
    reset_dispatch_queue_link_state,
    touch_run_session,
)
from dispatch_routing import ActiveSession, dispatch_queue_manager
from dispatch_session_policy import session_idle_ttl_seconds
from fastapi import HTTPException
from identity import Principal
from staff import claim_guard, dispatch_service
from staff.dispatch_service import DispatchCommand, dispatch_staff_run
from staff.plan import RunPlan
from staff.role_spec import RoleSpec

CALLER = Principal(id="operator-user", type="human", name="Operator", roles=["operator"], scopes=["staff.dispatch"])


def _reviewer_role() -> RoleSpec:
    return RoleSpec(
        name="code-reviewer",
        title="Reviewer",
        permissions={"code_read_only": True, "occupies_paths": False},
    )


def _impl_role() -> RoleSpec:
    return RoleSpec(name="ad-hoc", title="Impl", permissions={"code_read_only": False})


@pytest.fixture(autouse=True)
def _clean_queue() -> None:
    reset_dispatch_queue_link_state()
    yield
    reset_dispatch_queue_link_state()


def test_role_occupies_paths_defaults_from_code_read_only() -> None:
    assert _reviewer_role().occupies_paths is False
    assert _impl_role().occupies_paths is True
    explicit = RoleSpec(name="r", title="R", permissions={"code_read_only": True, "occupies_paths": True})
    assert explicit.occupies_paths is True


def test_read_only_try_dispatch_does_not_acquire_overlap_session() -> None:
    first = dispatch_queue_manager.try_dispatch(
        repository="Runner_Dashboard",
        prompt="Review `backend/foo.py`",
        occupies_paths=False,
        role="code-reviewer",
    )
    assert first.dispatched is True
    assert first.session is None
    assert dispatch_queue_manager.get_active_sessions() == []

    second = dispatch_queue_manager.try_dispatch(
        repository="Runner_Dashboard",
        prompt="Also touch `backend/foo.py`",
        occupies_paths=True,
        role="ad-hoc",
        number=1,
    )
    assert second.session is not None
    assert dispatch_queue_manager.get_active_sessions()


def test_reviewer_admitted_while_implementation_holds_overlapping_paths() -> None:
    impl = dispatch_queue_manager.try_dispatch(
        repository="Runner_Dashboard",
        prompt="Edit `backend/widget.py`",
        occupies_paths=True,
        role="ad-hoc",
        number=9,
    )
    assert impl.session is not None
    review = dispatch_queue_manager.try_dispatch(
        repository="Runner_Dashboard",
        prompt="Review `backend/widget.py` on PR #42",
        occupies_paths=False,
        role="code-reviewer",
    )
    assert review.dispatched is True
    assert review.session is None


def test_idle_expiry_releases_unbound_stale_session() -> None:
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    session_id = uuid4().hex
    dispatch_queue_manager._active_sessions[session_id] = ActiveSession(
        session_id=session_id,
        repository="Runner_Dashboard",
        number=None,
        declared_paths=["backend/a.py"],
        tier="cli",
        model="sonnet",
        dispatched_at=old,
        last_activity_at=old,
        role="ad-hoc",
        occupies_paths=True,
    )
    released = dispatch_queue_manager.expire_stale_sessions(now=datetime.now(UTC), ttl_seconds=60)
    assert released == [(session_id, "idle_ttl_exceeded")]
    assert dispatch_queue_manager.get_active_sessions() == []


def test_expire_keeps_session_when_bound_run_liveness_unknown() -> None:
    session_id = uuid4().hex
    recent = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    dispatch_queue_manager._active_sessions[session_id] = ActiveSession(
        session_id=session_id,
        repository="Runner_Dashboard",
        number=None,
        declared_paths=["*"],
        tier="cli",
        model="sonnet",
        role="ad-hoc",
        occupies_paths=True,
        run_id="run-missing",
        last_activity_at=recent,
        dispatched_at=recent,
    )
    bind_run_session("run-missing", session_id)

    def live(run_id: str) -> bool | None:
        return None if run_id == "run-missing" else True

    released = dispatch_queue_manager.expire_stale_sessions(run_live=live, ttl_seconds=60 * 60)
    assert released == []
    assert any(s.session_id == session_id for s in dispatch_queue_manager.get_active_sessions())


def test_expire_releases_session_when_bound_run_is_terminal() -> None:
    session_id = uuid4().hex
    dispatch_queue_manager._active_sessions[session_id] = ActiveSession(
        session_id=session_id,
        repository="Runner_Dashboard",
        number=None,
        declared_paths=["*"],
        tier="cli",
        model="sonnet",
        role="code-reviewer",
        occupies_paths=True,
        run_id="run-failed",
    )
    bind_run_session("run-failed", session_id)

    def live(run_id: str) -> bool | None:
        return False if run_id == "run-failed" else None

    released = dispatch_queue_manager.expire_stale_sessions(run_live=live)
    assert any(r[0] == session_id and r[1] == "bound_run_terminal" for r in released)
    assert dispatch_queue_manager.get_active_sessions() == []


def test_touch_run_session_refreshes_last_activity() -> None:
    attempt = dispatch_queue_manager.try_dispatch(
        repository="org/repo",
        prompt="`backend/x.py`",
        occupies_paths=True,
    )
    assert attempt.session is not None
    sid = attempt.session.session_id
    before = attempt.session.last_activity_at
    bind_run_session("run-1", sid)
    touch_run_session("run-1")
    after = dispatch_queue_manager._active_sessions[sid].last_activity_at
    assert after >= before


@pytest.mark.asyncio
async def test_failed_review_dispatch_leaves_no_blocking_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact failure mode: PR review without issue must not leak overlap sessions."""
    premise = type("P", (), {"allowed": True, "reason": "", "detail": "", "comment": None, "should_close": False})()

    async def allow_premise(*_a: Any, **_k: Any) -> Any:
        return premise

    runner_roles = {"code-reviewer": _reviewer_role()}

    class FakeRunner:
        machine = "test-node"

        def roles(self) -> dict[str, RoleSpec]:
            return runner_roles

        def plan(self, _req: Any) -> RunPlan:
            return RunPlan(
                role="code-reviewer",
                provider="codex",
                model=None,
                repo="UpstreamDrift",
                target_kind="pr",
                target_ref="PR #99",
                operator_prompt="",
                prompt="Review `backend/foo.py`",
                argv=["codex"],
                branch="",
                lease_ritual=False,
            )

        def admit(self, _req: Any, run_id: str = "") -> tuple[Any, RunPlan, bool]:
            rec = SimpleNamespace(
                id=run_id or "run-review-fail",
                role="code-reviewer",
                provider="codex",
                repo="UpstreamDrift",
                target_ref="PR #99",
                status="queued",
            )
            plan = self.plan(_req)
            return rec, plan, True

        def launch(self, _rec: Any, _plan: RunPlan) -> None:
            raise RuntimeError("stale submodule index.lock")

        store = SimpleNamespace(
            append_event=lambda *a, **k: None,
            update_run=lambda *a, **k: None,
        )

    fake = FakeRunner()

    monkeypatch.setattr("dispatch_premise.evaluate_dispatch_premise", allow_premise)
    monkeypatch.setattr("staff.preflight_duplicate_guard.check_duplicate_pr_or_run", AsyncMock())
    monkeypatch.setattr(claim_guard, "issue_attempt_blocker", lambda *a, **k: None)
    monkeypatch.setattr(claim_guard, "enforce_claim_guard", AsyncMock(return_value={"claim_check": "clear"}))
    monkeypatch.setattr(dispatch_service, "record_audit", lambda **_k: 1)
    monkeypatch.setattr(dispatch_service, "get_runner", lambda: fake)
    monkeypatch.setattr(
        "staff.provider_dispatch_preflight.refusal_for_provider_auth",
        lambda *_a, **_k: None,
    )

    cmd = DispatchCommand(
        role="code-reviewer",
        repo="UpstreamDrift",
        pr=99,
        prompt="Review `backend/foo.py`",
        requested_by="operator-user",
        provider="codex",
    )
    with pytest.raises(HTTPException) as err:
        await dispatch_staff_run(cmd, CALLER)
    assert err.value.status_code == 503
    assert dispatch_queue_manager.get_active_sessions() == []


def test_default_idle_ttl_is_forty_five_minutes() -> None:
    assert session_idle_ttl_seconds() == 45 * 60