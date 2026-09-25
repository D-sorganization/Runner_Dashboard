"""#1448: run cancel / rerun maintenance operations call GitHub for real.

Contract:
- Operations run in an anyio worker thread and reach ``gh_client`` on the event loop
  (``staff.maintenance_github.call_github``); called anywhere else they fail visibly as
  ``bridge_unavailable`` rather than blocking or silently skipping.
- GitHub faults are classified: token rejected -> ``auth_expired``, timeout ->
  ``peer_timeout``, any other refusal -> ``upstream_error``. Nothing reports success
  unless GitHub accepted the request.
- Verifiers read the run's real state: a cancel is verified only when the run concluded
  ``cancelled``; a rerun only when ``run_attempt`` advanced.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import anyio
import anyio.to_thread
import gh_client
import pytest
from identity import Principal
from staff import maintenance_github
from staff.actions import ActionContext
from staff.audit import reset_audit_store
from staff.maintenance import (
    MaintenancePreconditionError,
    execute_maintenance,
    reset_maintenance_cooldowns,
    verify_maintenance,
)

OWNER = Principal(id="owner", type="human", name="Owner", roles=["owner"], scopes=["staff.approve"])
CTX = ActionContext(thread_id="th-gh", caller=OWNER)
REPO = "Runner_Dashboard"
FULL = f"{maintenance_github.ORG}/{REPO}"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_gh.sqlite3"))
    monkeypatch.setenv("STAFF_RUNS_DIR", str(tmp_path))
    monkeypatch.setattr(maintenance_github, "POLL_INTERVAL_SECONDS", 0)
    reset_audit_store()
    reset_maintenance_cooldowns()
    yield
    reset_audit_store()
    reset_maintenance_cooldowns()


def in_worker(fn: Callable[..., Any], *args: Any) -> Any:
    """Run ``fn`` the way the routes do: in an anyio worker thread under a live loop."""

    async def main() -> Any:
        return await anyio.to_thread.run_sync(lambda: fn(*args))

    return anyio.run(main)


def runs(*states: dict[str, Any]) -> AsyncMock:
    """A fake ``gh_client.get`` returning successive run states (the last one repeats)."""
    seq = list(states)

    async def fake(path: str) -> dict[str, Any]:
        assert path == f"/repos/{FULL}/actions/runs/42"
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return AsyncMock(side_effect=fake)


def test_cancel_calls_github_and_is_verified_by_the_run_conclusion() -> None:
    cancel = AsyncMock(return_value=None)
    get = runs({"status": "in_progress"}, {"status": "completed", "conclusion": "cancelled"})
    params = {"repo": REPO, "run_id": 42}
    with patch.object(gh_client, "cancel_run", cancel), patch.object(gh_client, "get", get):
        res = in_worker(execute_maintenance, "maintenance.run_cancel", params, CTX)
        ok, msg = in_worker(verify_maintenance, res, params, CTX, "maintenance.run_cancel")
    cancel.assert_awaited_once_with(FULL, 42)
    assert res.success is True, res.error
    assert res.result["cancel_requested"] is True
    assert ok is True, msg


def test_cancel_that_lost_the_race_is_a_verification_mismatch() -> None:
    get = runs({"status": "completed", "conclusion": "success"})
    params = {"repo": REPO, "run_id": 42}
    with patch.object(gh_client, "cancel_run", AsyncMock()), patch.object(gh_client, "get", get):
        res = in_worker(execute_maintenance, "maintenance.run_cancel", params, CTX)
        ok, msg = in_worker(verify_maintenance, res, params, CTX, "maintenance.run_cancel")
    assert ok is False
    assert "success" in msg


@pytest.mark.parametrize(
    "failed_only,wrapper",
    [(True, "rerun_failed"), (False, "rerun")],
)
def test_rerun_uses_the_requested_scope_and_verifies_a_new_attempt(failed_only: bool, wrapper: str) -> None:
    call = AsyncMock(return_value=None)
    get = runs({"status": "completed", "run_attempt": 1}, {"status": "queued", "run_attempt": 2})
    params = {"repo": REPO, "run_id": 42, "failed_only": failed_only}
    with patch.object(gh_client, wrapper, call), patch.object(gh_client, "get", get):
        res = in_worker(execute_maintenance, "maintenance.run_rerun", params, CTX)
        ok, msg = in_worker(verify_maintenance, res, params, CTX, "maintenance.run_rerun")
    call.assert_awaited_once_with(FULL, 42)
    assert res.success is True, res.error
    assert res.result["previous_attempt"] == 1
    assert ok is True, msg


def test_cancel_and_rerun_waits_for_the_cancel_to_settle_before_rerunning() -> None:
    order: list[str] = []

    async def cancel(repo: str, run_id: int) -> None:
        order.append("cancel")

    async def rerun(repo: str, run_id: int) -> None:
        order.append("rerun")

    states = [
        {"status": "in_progress", "run_attempt": 1},
        {"status": "completed", "conclusion": "cancelled", "run_attempt": 1},
    ]

    async def get(path: str) -> dict[str, Any]:
        order.append("get")
        return states.pop(0) if len(states) > 1 else states[0]

    params = {"repo": REPO, "run_id": 42}
    with (
        patch.object(gh_client, "cancel_run", cancel),
        patch.object(gh_client, "rerun", rerun),
        patch.object(gh_client, "get", get),
    ):
        res = in_worker(execute_maintenance, "maintenance.cancel_and_rerun", params, CTX)
    assert res.success is True, res.error
    assert order.index("rerun") > order.index("cancel")
    # The rerun was issued only after a poll saw the run completed.
    assert order[: order.index("rerun")].count("get") >= 2


@pytest.mark.parametrize(
    "exc,failure_class",
    [
        (gh_client.GhAuthError("token expired"), "auth_expired"),
        (gh_client.GhClientError("timed out", kind="timeout"), "peer_timeout"),
        (gh_client.GhNotFound("/repos/x/actions/runs/42/cancel"), "upstream_error"),
        (gh_client.GhServerError(409, "/cancel", "Cannot cancel a completed run"), "upstream_error"),
    ],
)
def test_github_faults_are_classified_and_never_report_success(exc: Exception, failure_class: str) -> None:
    with patch.object(gh_client, "cancel_run", AsyncMock(side_effect=exc)):
        res = in_worker(execute_maintenance, "maintenance.run_cancel", {"repo": REPO, "run_id": 42}, CTX)
    assert res.success is False
    assert res.failure_class == failure_class
    assert res.error


def test_outside_a_worker_thread_the_operation_fails_visibly() -> None:
    with patch.object(gh_client, "cancel_run", AsyncMock()) as cancel:
        res = execute_maintenance("maintenance.run_cancel", {"repo": REPO, "run_id": 42}, CTX)
    assert res.success is False
    assert res.failure_class == "bridge_unavailable"
    cancel.assert_not_awaited()


def test_a_missing_repo_is_refused_before_github_is_called() -> None:
    with (
        patch.object(gh_client, "cancel_run", AsyncMock()) as cancel,
        pytest.raises(MaintenancePreconditionError, match="repo"),
    ):
        in_worker(execute_maintenance, "maintenance.run_cancel", {"run_id": 42}, CTX)
    cancel.assert_not_awaited()


@pytest.mark.parametrize("repo", ["someone-else/Runner_Dashboard", "../etc", "a&b"])
def test_repos_outside_the_org_or_malformed_are_refused(repo: str) -> None:
    with (
        patch.object(gh_client, "cancel_run", AsyncMock()) as cancel,
        pytest.raises(MaintenancePreconditionError),
    ):
        in_worker(execute_maintenance, "maintenance.run_cancel", {"repo": repo, "run_id": 42}, CTX)
    cancel.assert_not_awaited()


def test_an_org_qualified_repo_is_accepted() -> None:
    cancel = AsyncMock(return_value=None)
    with patch.object(gh_client, "cancel_run", cancel):
        res = in_worker(execute_maintenance, "maintenance.run_cancel", {"repo": FULL, "run_id": 42}, CTX)
    assert res.success is True, res.error
    cancel.assert_awaited_once_with(FULL, 42)
