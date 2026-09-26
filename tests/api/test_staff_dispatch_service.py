"""One dispatch policy for the /run route and the ``staff.dispatch`` action (#1487).

The approved-proposal executor used to call ``runner.submit`` directly, skipping peer
forwarding, rate limits, dry-run and the dispatch audit. Both paths now go through
``staff.dispatch_service.dispatch_staff_run``; these tests drive the executor the way
the proposal routes do (in an anyio worker thread) and reuse the /run fixtures.
"""

from __future__ import annotations

from typing import Any

import anyio.to_thread
import pytest
from fastapi import HTTPException
from identity import Principal
from staff import dispatch_service
from staff import fleet as fleet_mod
from staff import runner as runner_mod
from staff.action_executors import execute_staff_dispatch
from staff.actions import ActionContext, ActionResult
from staff.audit import get_audit_store, reset_audit_store
from staff.rate_limit import reset_rate_limiter

from tests.api.test_staff_fleet import peers, staff  # noqa: F401  (shared /run fixtures)

APPROVER = Principal(
    id="operator-user",
    type="human",
    name="Operator",
    roles=["operator"],
    scopes=["staff.approve", "staff.dispatch"],
)


@pytest.fixture(autouse=True)
def _isolated_policy_state() -> Any:
    reset_audit_store()
    reset_rate_limiter()
    yield
    reset_audit_store()
    reset_rate_limiter()


def _ctx(**overrides: Any) -> ActionContext:
    return ActionContext(**{"thread_id": "th_1", "proposal_id": "prop_1", "caller": APPROVER, **overrides})


async def _execute(params: dict[str, Any], ctx: ActionContext | None = None) -> ActionResult:
    """Run the executor as the proposal routes do: sync code in an anyio worker thread."""
    return await anyio.to_thread.run_sync(execute_staff_dispatch, params, ctx or _ctx())


@pytest.mark.unit
async def test_approved_dispatch_to_a_peer_is_forwarded(
    staff: runner_mod.StaffRunner,  # noqa: F811
    peers: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def fake_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        seen.update({"url": url, "body": body, "headers": headers})
        return 200, {"dry_run": False, "run": {"id": "run_peer", "role": "ad-hoc", "status": "queued"}}

    monkeypatch.setattr(fleet_mod, "post_json", fake_post)
    res = await _execute({"role": "ad-hoc", "prompt": "hi", "machine": "oglaptop"})

    assert res.success, res.error
    assert res.run_id == "run_peer"
    assert res.result["forwarded_to"] == "OGLaptop"
    assert seen["url"] == "http://og:8321/api/staff/ad-hoc/run"
    assert seen["body"]["machine"] == "local"
    assert "X-Staff-On-Behalf-Of" in seen["headers"]
    assert staff.store.list_runs(limit=5) == []  # nothing ran locally


@pytest.mark.unit
async def test_approved_local_dispatch_is_audited_with_thread_provenance(
    staff: runner_mod.StaffRunner,  # noqa: F811
) -> None:
    res = await _execute({"role": "ad-hoc", "prompt": "hi"})

    assert res.success, res.error
    rec = staff.store.get_run(res.run_id or "")
    assert rec is not None and rec.thread_id == "th_1"
    entries = get_audit_store().list_entries(action="dispatch")
    assert len(entries) == 1
    assert entries[0].run_id == res.run_id
    assert entries[0].surface == "thread" and entries[0].thread_id == "th_1"


@pytest.mark.unit
async def test_approved_dispatch_honours_dry_run(staff: runner_mod.StaffRunner) -> None:  # noqa: F811
    res = await _execute({"role": "ad-hoc", "prompt": "hi"}, _ctx(dry_run=True))

    assert res.success, res.error
    assert res.run_id is None and res.result["dry_run"] is True
    assert staff.store.list_runs(limit=5) == []
    assert get_audit_store().list_entries(action="dispatch") == []


@pytest.mark.unit
async def test_approved_dispatch_is_rate_limited_like_the_route(
    staff: runner_mod.StaffRunner,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buckets: list[str] = []

    def refuse(action: str, principal: Principal) -> None:
        buckets.append(action)
        raise HTTPException(status_code=429, detail={"code": "rate_limited", "message": "slow down"})

    monkeypatch.setattr(dispatch_service, "check_rate_limit", refuse)
    res = await _execute({"role": "ad-hoc", "prompt": "hi"})

    assert buckets == ["dispatches"]
    assert not res.success and res.failure_class == "rate_limited"
    assert "slow down" in (res.error or "")
    assert staff.store.list_runs(limit=5) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("params", "failure_class"),
    [
        ({"role": "ad-hoc", "prompt": "hi", "machine": "Mars"}, "invalid_params"),
        ({"role": "no-such-role", "prompt": "hi"}, "invalid_params"),
        ({"prompt": "hi"}, "invalid_params"),
    ],
    ids=["unknown-machine", "unknown-role", "missing-role"],
)
async def test_invalid_dispatch_fails_visibly(
    staff: runner_mod.StaffRunner,  # noqa: F811
    peers: dict[str, str],  # noqa: F811
    params: dict[str, Any],
    failure_class: str,
) -> None:
    res = await _execute(params)

    assert not res.success and res.failure_class == failure_class and res.error
    assert staff.store.list_runs(limit=5) == []


@pytest.mark.unit
async def test_unreachable_peer_fails_visibly(
    staff: runner_mod.StaffRunner,  # noqa: F811
    peers: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def dead_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        raise ConnectionError("down")

    monkeypatch.setattr(fleet_mod, "post_json", dead_post)
    res = await _execute({"role": "ad-hoc", "prompt": "hi", "machine": "OGLaptop"})

    assert not res.success and res.failure_class == "peer_unreachable"
    assert "OGLaptop" in (res.error or "")


@pytest.mark.unit
def test_executor_outside_a_worker_thread_fails_visibly(staff: runner_mod.StaffRunner) -> None:  # noqa: F811
    res = execute_staff_dispatch({"role": "ad-hoc", "prompt": "hi"}, _ctx())

    assert not res.success and res.failure_class == "bridge_unavailable"
    assert staff.store.list_runs(limit=5) == []


@pytest.mark.unit
def test_command_contract_rejects_an_empty_role() -> None:
    with pytest.raises(ValueError, match="role"):
        dispatch_service.DispatchCommand(role=" ", requested_by="x")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("result", "run_id", "ok"),
    [
        ({"forwarded_to": "OGLaptop", "dry_run": False}, "run_peer", True),
        ({"forwarded_to": None, "dry_run": True}, None, True),
        ({"forwarded_to": None, "dry_run": False}, None, False),
        ({"forwarded_to": None, "dry_run": False}, "run_missing", False),
    ],
    ids=["forwarded", "dry-run", "no-run-id", "local-run-missing"],
)
def test_verifier_accepts_forwarded_and_dry_runs_only_with_evidence(
    staff: runner_mod.StaffRunner,  # noqa: F811
    result: dict[str, Any],
    run_id: str | None,
    ok: bool,
) -> None:
    from staff.action_executors import verify_staff_dispatch

    res = ActionResult(success=True, result=result, run_id=run_id)
    verified, message = verify_staff_dispatch(res, {}, _ctx())
    assert verified is ok, message


@pytest.mark.unit
@pytest.mark.parametrize(
    ("verification", "detail", "ok", "fragment"),
    [
        ("", "", True, "not yet verified"),
        ("unverified", "PR #4 head CI is still pending", True, "unverified"),
        ("verified", "PR #4 is open with head CI green", True, "verified: PR #4"),
        ("failed", "no pull request found for branch b", False, "no pull request"),
    ],
)
def test_verifier_reports_the_runs_output_verification(
    staff: runner_mod.StaffRunner,  # noqa: F811
    verification: str,
    detail: str,
    ok: bool,
    fragment: str,
) -> None:
    """#1516: the dispatch verifier reports the run's post-run verification, not only that it exists."""
    from staff.action_executors import verify_staff_dispatch
    from staff.store import RunRecord

    staff.store.create_run(
        RunRecord(
            id="run_local",
            role="ad-hoc",
            provider="fake",
            model=None,
            machine="TestNode",
            repo="R",
            target_kind="prompt",
            target_ref="",
            prompt="p",
            status="succeeded",
            verification=verification,
            verification_detail=detail,
        )
    )
    res = ActionResult(success=True, result={"forwarded_to": None, "dry_run": False}, run_id="run_local")

    verified, message = verify_staff_dispatch(res, {}, _ctx())

    assert verified is ok, message
    assert fragment in message
