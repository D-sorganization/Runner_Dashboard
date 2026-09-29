"""Dispatch admission is audited before any worker starts (BR-02, Issue #1796).

``dispatch_staff_run`` used to call ``runner.submit`` (row + daemon worker) and only
then write the fail-closed audit, so an audit outage returned an error while the
provider was already running. Admission now goes audit → queued row → worker, and an
operation id admits one run however often it is retried.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from identity import Principal
from staff import dispatch_service
from staff import fleet as fleet_mod
from staff import runner as runner_mod
from staff.audit import AuditError, get_audit_store, reset_audit_store
from staff.dispatch_service import DispatchCommand, dispatch_staff_run
from staff.rate_limit import reset_rate_limiter

from tests.api.test_staff_fleet import peers, staff  # noqa: F401  (shared /run fixtures)

CALLER = Principal(id="operator-user", type="human", name="Operator", roles=["operator"], scopes=["staff.dispatch"])


@pytest.fixture(autouse=True)
def _isolated_policy_state() -> Any:
    reset_audit_store()
    reset_rate_limiter()
    yield
    reset_audit_store()
    reset_rate_limiter()


@pytest.fixture
def launches(staff: runner_mod.StaffRunner, monkeypatch: pytest.MonkeyPatch) -> list[str]:  # noqa: F811
    started: list[str] = []
    monkeypatch.setattr(staff, "launch", lambda rec, plan: started.append(rec.id))
    return started


def _cmd(**overrides: Any) -> DispatchCommand:
    return DispatchCommand(**{"role": "ad-hoc", "requested_by": "operator-user", "prompt": "hi", **overrides})


@pytest.mark.unit
async def test_audit_outage_admits_nothing_and_starts_no_worker(
    staff: runner_mod.StaffRunner,  # noqa: F811
    launches: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def audit_down(**_kwargs: Any) -> int:
        raise AuditError("audit database is locked")

    monkeypatch.setattr(dispatch_service, "record_audit", audit_down)

    with pytest.raises(HTTPException) as err:
        await dispatch_staff_run(_cmd(), CALLER)

    assert err.value.status_code == 503
    assert err.value.detail["code"] == "audit_unavailable"
    assert launches == []
    assert staff.store.list_runs(limit=5) == []


@pytest.mark.unit
async def test_audit_is_written_before_the_row_and_the_worker(
    staff: runner_mod.StaffRunner,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    real_audit, real_admit = dispatch_service.record_audit, staff.admit

    def audit(**kwargs: Any) -> int:
        order.append(f"audit:{kwargs['outcome']}")
        return real_audit(**kwargs)

    def admit(req: Any, **kwargs: Any) -> Any:
        order.append("admit")
        return real_admit(req, **kwargs)

    monkeypatch.setattr(dispatch_service, "record_audit", audit)
    monkeypatch.setattr(staff, "admit", admit)
    monkeypatch.setattr(staff, "launch", lambda rec, plan: order.append("launch"))

    out = await dispatch_staff_run(_cmd(), CALLER)

    assert order == ["audit:admitted", "admit", "launch"]
    entry = get_audit_store().list_entries(action="dispatch")[0]
    assert entry.run_id == out["run"]["id"]


@pytest.mark.unit
async def test_one_operation_id_admits_and_launches_one_run(
    staff: runner_mod.StaffRunner,  # noqa: F811
    launches: list[str],
) -> None:
    first = await dispatch_staff_run(_cmd(operation_id="op-0123456789ab"), CALLER)
    again = await dispatch_staff_run(_cmd(operation_id="op-0123456789ab"), CALLER)

    assert first["run"]["id"] == again["run"]["id"] == "run-0123456789ab"
    assert launches == ["run-0123456789ab"]
    assert len(staff.store.list_runs(limit=5)) == 1


@pytest.mark.unit
async def test_launch_failure_leaves_a_failed_run_under_the_same_id(
    staff: runner_mod.StaffRunner,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_threads(rec: Any, plan: Any) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(staff, "launch", no_threads)

    with pytest.raises(HTTPException) as err:
        await dispatch_staff_run(_cmd(operation_id="op-feedfacecafe"), CALLER)

    assert err.value.status_code == 503
    assert err.value.detail["code"] == "launch_failed"
    assert err.value.detail["run_id"] == "run-feedfacecafe"
    rec = staff.store.get_run("run-feedfacecafe")
    assert rec is not None and rec.status == "failed" and rec.failure_class == "launch_failed"


@pytest.mark.unit
async def test_forwarded_dispatch_carries_the_operation_id(
    staff: runner_mod.StaffRunner,  # noqa: F811
    peers: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies: list[dict[str, Any]] = []

    async def fake_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        bodies.append(body)
        return 200, {"dry_run": False, "run": {"id": "run-0123456789ab", "role": "ad-hoc", "status": "queued"}}

    monkeypatch.setattr(fleet_mod, "post_json", fake_post)
    await dispatch_staff_run(_cmd(machine="oglaptop", operation_id="op-0123456789ab"), CALLER)
    await dispatch_staff_run(_cmd(machine="oglaptop"), CALLER)

    assert bodies[0]["operation_id"] == "op-0123456789ab"
    assert "operation_id" not in bodies[1]  # peers on older builds never see an empty field


@pytest.mark.unit
async def test_only_a_fleet_peer_may_name_the_operation(
    staff: runner_mod.StaffRunner,  # noqa: F811
    launches: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from routers.staff import RunBody, run_dispatch

    body = RunBody(prompt="hi", operation_id="run-0123456789ab")
    request: Any = SimpleNamespace(headers={})

    monkeypatch.setattr(fleet_mod, "is_fleet_peer", lambda _p: False)
    from_user = await run_dispatch("ad-hoc", body, request, CALLER)
    monkeypatch.setattr(fleet_mod, "is_fleet_peer", lambda _p: True)
    from_peer = await run_dispatch("ad-hoc", body, request, CALLER)

    assert from_user["run"]["id"] != "run-0123456789ab"
    assert from_peer["run"]["id"] == "run-0123456789ab"
