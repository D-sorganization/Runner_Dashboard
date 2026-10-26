"""Barb-authored staff dispatch is fenced on the lease epoch, atomically with admission (#2734)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from identity import Principal
from staff import runner as runner_mod
from staff.audit import reset_audit_store
from staff.barb_continuity import BarbContinuityStore
from staff.barb_lease_fence import BarbClaim
from staff.dispatch_service import DispatchCommand, dispatch_staff_run
from staff.rate_limit import reset_rate_limiter

from tests.api.test_staff_fleet import peers, staff  # noqa: F401  (shared /run fixtures)

SCOPE = "barb:dispatch-coordinator"
CALLER = Principal(id="agent-alpha", type="bot", name="Alpha", roles=["bot"], scopes=["staff.dispatch"])


@pytest.fixture(autouse=True)
def _isolated_policy_state() -> Any:
    reset_audit_store()
    reset_rate_limiter()
    yield
    reset_audit_store()
    reset_rate_limiter()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BarbContinuityStore:
    from staff import barb_continuity  # noqa: PLC0415

    s = BarbContinuityStore(tmp_path / "barb.sqlite3")
    monkeypatch.setattr(barb_continuity, "_DEFAULT_STORE", s)
    return s


@pytest.fixture
def launches(staff: runner_mod.StaffRunner, monkeypatch: pytest.MonkeyPatch) -> list[str]:  # noqa: F811
    started: list[str] = []
    monkeypatch.setattr(staff, "launch", lambda rec, plan: started.append(rec.id))
    return started


def _cmd() -> DispatchCommand:
    return DispatchCommand(role="ad-hoc", requested_by="agent-alpha", prompt="hi")


def _claim(epoch: int, caller: str = "agent-alpha") -> BarbClaim:
    return BarbClaim(scope=SCOPE, epoch=epoch, caller_id=caller)


@pytest.mark.unit
async def test_current_epoch_dispatch_is_admitted(
    store: BarbContinuityStore,
    staff: runner_mod.StaffRunner,  # noqa: F811
    launches: list[str],
) -> None:
    lease = store.checkout_lease(scope=SCOPE, holder="agent-alpha")["lease"]
    out = await dispatch_staff_run(_cmd(), CALLER, barb_claim=_claim(lease["epoch"]))
    assert out["run"]["id"] in launches


@pytest.mark.unit
async def test_stale_epoch_dispatch_admits_nothing(
    store: BarbContinuityStore,
    staff: runner_mod.StaffRunner,  # noqa: F811
    launches: list[str],
) -> None:
    lease = store.checkout_lease(scope=SCOPE, holder="agent-alpha")["lease"]
    store.checkout_lease(scope=SCOPE, holder="op-1", force=True)
    with pytest.raises(HTTPException) as err:
        await dispatch_staff_run(_cmd(), CALLER, barb_claim=_claim(lease["epoch"]))
    assert err.value.status_code == 409
    assert err.value.detail["error"] == "stale_barb_lease"
    assert err.value.detail["current_epoch"] == lease["epoch"] + 1
    assert launches == []
    assert staff.store.list_runs(limit=5) == []


@pytest.mark.unit
async def test_takeover_between_check_and_admit_is_blocked_by_the_lock(
    store: BarbContinuityStore,
    staff: runner_mod.StaffRunner,  # noqa: F811
    launches: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fire a takeover from another thread while ``admit`` runs: it must wait, so admission is consistent."""
    import threading  # noqa: PLC0415

    lease = store.checkout_lease(scope=SCOPE, holder="agent-alpha")["lease"]
    real_admit = staff.admit
    taker_done = threading.Event()

    def admit(req: Any, **kwargs: Any) -> Any:
        t = threading.Thread(
            target=lambda: (store.checkout_lease(scope=SCOPE, holder="op-1", force=True), taker_done.set())
        )
        t.start()
        assert not taker_done.wait(0.3), "takeover landed inside the check+admit critical section"
        return real_admit(req, **kwargs)

    monkeypatch.setattr(staff, "admit", admit)
    out = await dispatch_staff_run(_cmd(), CALLER, barb_claim=_claim(lease["epoch"]))
    assert out["run"]["id"] in launches
    assert taker_done.wait(5)  # takeover proceeds right after the write


@pytest.mark.unit
async def test_dispatch_without_claim_is_unaffected(
    store: BarbContinuityStore,
    staff: runner_mod.StaffRunner,  # noqa: F811
    launches: list[str],
) -> None:
    store.checkout_lease(scope=SCOPE, holder="op-1")
    out = await dispatch_staff_run(_cmd(), CALLER)
    assert out["run"]["id"] in launches
