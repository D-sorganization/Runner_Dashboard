"""One dispatch policy for the /run route and the ``staff.dispatch`` action (#1487).

The approved-proposal executor used to call ``runner.submit`` directly, skipping peer
forwarding, rate limits, dry-run and the dispatch audit. Both paths now go through
``staff.dispatch_service.dispatch_staff_run``; these tests drive the executor the way
the proposal routes do (in an anyio worker thread) and reuse the /run fixtures.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import anyio.to_thread
import ci_fix_dispatch
import ci_fix_service
import pytest
from ci_fix_locks import CIFixLockManager
from fastapi import HTTPException
from identity import Principal
from staff import dispatch_service
from staff import fleet as fleet_mod
from staff import runner as runner_mod
from staff.action_executors import execute_staff_dispatch
from staff.actions import ActionContext, ActionResult
from staff.adapters import ProviderAdapter
from staff.audit import get_audit_store, reset_audit_store
from staff.rate_limit import reset_rate_limiter

from tests.api.test_staff_fleet import peers, staff  # noqa: F401,F811  (shared /run fixtures)

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


async def _async_value(value: Any) -> Any:
    return value


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
    staff._adapters = {
        **staff._adapters,
        "codex": replace(staff._adapters["codex"], executable="rd-test-codex-not-installed"),
    }

    async def fake_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        seen.update({"url": url, "body": body, "headers": headers})
        return 200, {"dry_run": False, "run": {"id": "run_peer", "role": "ad-hoc", "status": "queued"}}

    monkeypatch.setattr(fleet_mod, "post_json", fake_post)
    res = await _execute({"role": "ad-hoc", "provider": "codex", "prompt": "hi", "machine": "oglaptop"})

    assert res.success, res.error
    assert res.run_id == "run_peer"
    assert res.result["forwarded_to"] == "OGLaptop"
    assert seen["url"] == "http://og:8321/api/staff/ad-hoc/run"
    assert seen["body"]["machine"] == "local"
    assert seen["body"]["provider"] == "codex"
    assert "model_tier" not in seen["body"]
    assert "X-Staff-On-Behalf-Of" in seen["headers"]
    assert staff.store.list_runs(limit=5) == []  # nothing ran locally


@pytest.mark.unit
async def test_auto_dispatch_routes_to_peer_before_local_provider_plan(
    staff: runner_mod.StaffRunner,  # noqa: F811
    peers: dict[str, str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An auto target with the primary installed must win even when the source lacks it."""
    seen: dict[str, Any] = {}
    staff._adapters = {
        **staff._adapters,
        "codex": replace(staff._adapters["codex"], executable="rd-test-codex-not-installed"),
    }

    async def fake_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        seen.update({"url": url, "body": body, "headers": headers})
        return 200, {"dry_run": False, "run": {"id": "run_peer_auto", "role": "ad-hoc", "status": "queued"}}

    board = {
        "machines": {
            "Desk": {
                "status": "online",
                "providers": {"codex": False, "claude": False, "antigravity": False},
                "running": [],
                "queued": [],
            },
            "OGLaptop": {
                "status": "online",
                "providers": {"codex": True, "claude": True, "antigravity": True},
                "running": [],
                "queued": [],
            },
        }
    }
    monkeypatch.setattr(fleet_mod, "post_json", fake_post)
    monkeypatch.setattr(fleet_mod, "aggregate_board", lambda *args: _async_value(board))

    res = await _execute({"role": "ad-hoc", "provider": "codex", "prompt": "hi", "machine": "auto"})

    assert res.success, res.error
    assert res.result["forwarded_to"] == "OGLaptop"
    assert seen["body"]["provider"] == "codex"
    assert seen["body"]["machine"] == "local"
    assert staff.store.list_runs(limit=5) == []


@pytest.mark.unit
async def test_auto_dispatch_keeps_local_target_after_provider_fallback(
    staff: runner_mod.StaffRunner,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staff._adapters = {
        **staff._adapters,
        "codex": replace(staff._adapters["codex"], executable="rd-test-codex-not-installed"),
    }
    selected: list[str] = []

    async def choose_local(runner: runner_mod.StaffRunner, machine: str, provider: str, *, role: Any = None) -> str:
        selected.append(provider)
        return "local"

    monkeypatch.setattr(dispatch_service, "_resolve_target", choose_local)
    monkeypatch.setattr(fleet_mod, "post_json", lambda *args, **kwargs: pytest.fail("unexpected peer dispatch"))

    result = await _execute({"role": "ad-hoc", "provider": "codex", "prompt": "hi", "machine": "auto"})

    assert result.success, result.error
    assert selected == ["codex"]
    assert staff.store.get_run(result.run_id or "") is not None


@pytest.mark.unit
async def test_remote_dispatch_refuses_internal_tier_until_peer_contract_supports_it(
    staff: runner_mod.StaffRunner,  # noqa: F811
    peers: dict[str, str],  # noqa: F811
) -> None:
    cmd = dispatch_service.DispatchCommand(
        role="ad-hoc",
        requested_by="operator-user",
        provider="codex",
        model_tier="routine",
        prompt="hi",
        machine="OGLaptop",
    )
    with pytest.raises(HTTPException, match="internal model_tier") as exc_info:
        await dispatch_service.dispatch_staff_run(cmd, APPROVER)
    assert exc_info.value.status_code == 422
    assert staff.store.list_runs(limit=5) == []


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
async def test_ci_fix_uses_generic_chain_when_routed_primary_is_not_installed(
    staff: runner_mod.StaffRunner,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """CI-fix keeps its route tier while the shared dispatcher selects an installed alternate."""
    import json

    from ci_fix_service import CIFixDispatchRequest
    from staff import failover_dispatch
    from staff.model_tiers import model_for_provider_tier
    from staff.provider_failover_config import resolve_failover_chain

    fake_codex = ProviderAdapter(
        provider_id="codex",
        label="Missing Codex test adapter",
        executable="rd-test-codex-not-installed",
        argv=("exec", "{prompt}"),
        default_model="gpt-6-luna",
    )
    staff._adapters = {
        **staff._adapters,
        "codex": fake_codex,
        "antigravity": replace(staff._adapters["antigravity"], executable="rd-test-agy-not-installed"),
    }
    monkeypatch.setattr(
        ci_fix_service,
        "available_providers",
        lambda: {"claude": True, "codex": False, "antigravity": False},
    )
    monkeypatch.setattr(ci_fix_service, "dispatch_staff_run", dispatch_service.dispatch_staff_run)
    monkeypatch.setattr(ci_fix_service, "GLOBAL_CI_FIX_LOCK_MGR", CIFixLockManager())
    monkeypatch.setattr(failover_dispatch, "_provider_auth_blocks", lambda provider, machine: False)
    monkeypatch.setattr(failover_dispatch, "provider_in_cooldown", lambda provider: False)
    role = staff.roles()["ad-hoc"]
    assert "claude" in role.providers
    assert failover_dispatch._usable_provider(staff._adapters, role, "claude")
    assert [step.provider for step in resolve_failover_chain(role, requested_provider="codex")] == [
        "codex",
        "claude",
        "antigravity",
    ]
    audit_path = tmp_path / "ci-fix-audit.json"
    monkeypatch.setattr(ci_fix_dispatch, "DEFAULT_AUDIT_PATH", audit_path)

    result = await ci_fix_service.dispatch_ci_fix(
        CIFixDispatchRequest(
            repo="Runner_Dashboard",
            pr_number=912,
            branch="feat/ci-fallback",
            workflow_name="Lint",
            log_tail="ruff failed",
        ),
        APPROVER,
        launcher=dispatch_service.dispatch_staff_run,
    )

    run = staff.store.get_run(result["staff_run_id"])
    assert run is not None
    assert run.provider == "claude"
    assert run.model == model_for_provider_tier("claude", "routine")
    assert result["launched_provider"] == run.provider
    assert result["launched_model"] == run.model
    audit = json.loads(audit_path.read_text(encoding="utf-8"))[0]
    assert audit["provider"] == run.provider
    assert audit["model"] == run.model


@pytest.mark.unit
async def test_approved_dispatch_honours_dry_run(staff: runner_mod.StaffRunner) -> None:  # noqa: F811
    res = await _execute({"role": "ad-hoc", "prompt": "hi"}, _ctx(dry_run=True))

    assert res.success, res.error
    assert res.run_id is None and res.result["dry_run"] is True
    assert staff.store.list_runs(limit=5) == []
    entries = get_audit_store().list_entries(action="dispatch")
    assert len(entries) == 1
    assert entries[0].outcome == "dry_run"
    assert entries[0].detail.get("dry_run") is True


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


@pytest.mark.asyncio
async def test_dispatch_staff_run_skips_when_premise_rejected():
    """RD-2 / #1847: dispatch_staff_run refuses dispatch when premise is rejected."""
    from unittest.mock import patch

    from fastapi import HTTPException
    from staff.dispatch_service import DispatchCommand, dispatch_staff_run

    cmd = DispatchCommand(
        role="code-reviewer",
        requested_by="operator",
        repo="D-sorganization/Runner_Dashboard",
        issue=42,
        prompt="Fix local-only-runner-guard.yml failure",
    )
    caller = Principal(id="operator", type="user", name="Operator")
    with patch(
        "dispatch_premise.evaluate_dispatch_premise",
        return_value=type(
            "P",
            (),
            {
                "allowed": False,
                "reason": "already_resolved_on_main",
                "detail": "check passing on main",
                "comment": "already resolved on main at d354634",
                "should_close": True,
            },
        )(),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await dispatch_staff_run(cmd, caller)
        assert exc_info.value.status_code == 409
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail["code"] == "premise_rejected"
        assert detail["reason"] == "already_resolved_on_main"
