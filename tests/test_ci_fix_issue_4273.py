"""Regression tests for #4273: CI-fix caps, stale head, hub TTL, cursor handoff, routed provider."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from ci_fix_attempt_store import CIFixAttemptStore, DEFAULT_PER_HEAD_CAP, DEFAULT_PER_PR_CAP
from ci_fix_dispatch import route_ci_fix
from ci_fix_locks import CIFixLockManager, DEFAULT_LOCK_TTL_SECONDS
from ci_fix_service import CIFixAttemptCapError, CIFixDispatchRequest, _route_and_launch
from identity import Principal


def test_default_lock_ttl_is_at_least_staff_run_wall_clock() -> None:
    """E-04: lock TTL default must cover a staff run wall clock, not a fixed 2h lease."""
    assert DEFAULT_LOCK_TTL_SECONDS >= 4 * 3600


def test_attempt_store_enforces_per_head_and_per_pr_caps(tmp_path: Path) -> None:
    store = CIFixAttemptStore(tmp_path / "locks.db")
    for _ in range(DEFAULT_PER_HEAD_CAP):
        cap = store.reserve_attempt("Runner_Dashboard", 9, "deadbeef")
        assert cap.allowed
    blocked = store.reserve_attempt("Runner_Dashboard", 9, "deadbeef")
    assert not blocked.allowed
    assert "per-head" in blocked.reason

    other_head = store.reserve_attempt("Runner_Dashboard", 9, "cafebabe")
    assert other_head.allowed
    for _ in range(DEFAULT_PER_PR_CAP - DEFAULT_PER_HEAD_CAP - 1):
        cap = store.reserve_attempt("Runner_Dashboard", 9, f"sha{_}")
        assert cap.allowed
    total_block = store.reserve_attempt("Runner_Dashboard", 9, "sha-final")
    assert not total_block.allowed
    assert "per-PR" in total_block.reason


def test_route_without_operator_approval_never_escalates_strong() -> None:
    route = route_ci_fix("test", attempt_number=5, max_attempts=2, allow_strong_escalation=False)
    assert route.tier != "strong"
    assert route.escalated is False

    strong = route_ci_fix("test", attempt_number=2, max_attempts=2, allow_strong_escalation=True)
    assert strong.tier == "strong"
    assert strong.escalated is True


@pytest.mark.asyncio
async def test_stale_head_blocks_attempt_reservation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = CIFixAttemptStore(tmp_path / "locks.db")
    monkeypatch.setattr("ci_fix_service.global_ci_fix_attempt_store", lambda: store)
    locks = CIFixLockManager(path=tmp_path / "locks.db")
    monkeypatch.setattr("ci_fix_service.GLOBAL_CI_FIX_LOCK_MGR", locks)
    monkeypatch.setattr(
        "ci_fix_service.available_providers",
        lambda: dict.fromkeys(("claude", "codex", "antigravity"), True),
    )

    req = CIFixDispatchRequest(
        repo="Runner_Dashboard",
        pr_number=1,
        branch="feat/fix",
        head_sha="aaa",
        log_tail="FAILED t.py::x",
    )
    caller = Principal(id="t", type="bot", name="t", scopes=["remediation.dispatch"])

    class FakeLauncher:
        async def __call__(self, cmd: object, _caller: Principal) -> dict:
            return {"run": {"id": "r1", "provider": "claude", "model": "m"}}

    for _ in range(DEFAULT_PER_HEAD_CAP):
        await _route_and_launch(req, caller, "sess", FakeLauncher())
        locks.release("Runner_Dashboard", 1)

    with pytest.raises(CIFixAttemptCapError, match="per-head"):
        await _route_and_launch(req, caller, "sess2", FakeLauncher())


@pytest.mark.asyncio
async def test_cursor_branch_emits_handoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = CIFixAttemptStore(tmp_path / "locks.db")
    monkeypatch.setattr("ci_fix_service.global_ci_fix_attempt_store", lambda: store)
    locks = CIFixLockManager(path=tmp_path / "locks.db")
    monkeypatch.setattr("ci_fix_service.GLOBAL_CI_FIX_LOCK_MGR", locks)
    monkeypatch.setattr(
        "ci_fix_service.available_providers",
        lambda: dict.fromkeys(("claude", "codex", "antigravity"), True),
    )

    req = CIFixDispatchRequest(
        repo="Runner_Dashboard",
        pr_number=2,
        branch="cursor/ci-fix",
        head_sha="bbb",
        log_tail="FAILED t.py::y",
    )
    caller = Principal(id="t", type="bot", name="t", scopes=["remediation.dispatch"])

    with patch("scripts.cursor_cloud_handoff.emit_handoff_checkin") as emit:
        emit.return_value = {"ok": True, "delivered": 1}
        result = await _route_and_launch(req, caller, "sess", lambda *a: {})
        emit.assert_called_once()
        assert result["launched_provider"] == "cursor_cloud"


@pytest.mark.asyncio
async def test_routed_provider_must_be_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ci_fix_service import CIFixLaunchUnavailableError

    store = CIFixAttemptStore(tmp_path / "locks.db")
    monkeypatch.setattr("ci_fix_service.global_ci_fix_attempt_store", lambda: store)
    locks = CIFixLockManager(path=tmp_path / "locks.db")
    monkeypatch.setattr("ci_fix_service.GLOBAL_CI_FIX_LOCK_MGR", locks)
    monkeypatch.setattr(
        "ci_fix_service.available_providers", lambda: {"claude": True, "codex": False, "antigravity": False}
    )

    req = CIFixDispatchRequest(
        repo="Runner_Dashboard",
        pr_number=3,
        branch="feat/x",
        head_sha="ccc",
        log_tail="ruff failed",
        workflow_name="lint",
    )
    caller = Principal(id="t", type="bot", name="t", scopes=["remediation.dispatch"])

    with pytest.raises(CIFixLaunchUnavailableError, match="codex"):
        await _route_and_launch(req, caller, "sess", lambda *a: {})
