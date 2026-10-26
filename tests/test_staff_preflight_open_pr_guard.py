"""Unit tests for staff pre-dispatch duplicate-PR and active-run guards (#2254)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from identity import Principal
from staff.dispatch_service import DispatchCommand, dispatch_staff_run
from staff.preflight_duplicate_guard import check_duplicate_pr_or_run, is_issue_referenced_in_pr
from staff.roles import RoleSpec
from staff.runner import StaffRunner
from staff.store import RunRecord, RunStore


@pytest.fixture
def test_principal() -> Principal:
    return Principal(id="test:operator", type="human", name="Operator", roles=["operator"], scopes=["staff.run"])


@pytest.fixture
def mock_runner(tmp_path) -> StaffRunner:
    store = RunStore(tmp_path / "staff.sqlite3")
    runner = MagicMock(spec=StaffRunner)
    runner.machine = "local"
    runner.store = store
    role = RoleSpec(
        name="issue-remediator",
        title="Issue Remediator",
        providers=("claude",),
        budget_usd_per_day=10.0,
    )
    runner.roles.return_value = {"issue-remediator": role, "pr-remediator": role}
    plan_mock = MagicMock()
    plan_mock.repo = "Test_Repo"
    plan_mock.issue = 123
    plan_mock.pr = None
    plan_mock.provider = "claude"
    plan_mock.model = "sonnet"
    plan_mock.machine = "local"
    plan_mock.prompt = "Fix the bug"
    plan_mock.task_type = ""
    plan_mock.standards_extra = ""
    plan_mock.requested_skill = None
    plan_mock.to_dict.return_value = {"role": "issue-remediator", "repo": "Test_Repo", "issue": 123}
    runner.plan.return_value = plan_mock
    return runner


def test_is_issue_referenced_in_pr_branch_and_title_and_body() -> None:
    # 1. Branch pattern matching <N>-, fix/<N>-, issue-<N>
    assert is_issue_referenced_in_pr({"head": {"ref": "fix/123-bug"}}, 123)
    assert is_issue_referenced_in_pr({"head": {"ref": "123-bug"}}, 123)
    assert is_issue_referenced_in_pr({"head": {"ref": "feat/issue-123"}}, 123)
    assert is_issue_referenced_in_pr({"head": {"ref": "staff/issue-remediator-123-abcdef"}}, 123)
    assert not is_issue_referenced_in_pr({"head": {"ref": "fix/1234-other-bug"}}, 123)

    # 2. Title matching #<N>, RM #<N>, issue #<N>
    assert is_issue_referenced_in_pr({"title": "fix(core): resolve RM #123"}, 123)
    assert is_issue_referenced_in_pr({"title": "feat: something (#123)"}, 123)
    assert not is_issue_referenced_in_pr({"title": "feat: something #1234"}, 123)

    # 3. Body matching Closes #<N>, Fixes #<N>
    assert is_issue_referenced_in_pr({"body": "Closes #123 cleanly"}, 123)
    assert is_issue_referenced_in_pr({"body": "Fixes #123\nSome detail"}, 123)
    assert not is_issue_referenced_in_pr({"body": "Unrelated PR text"}, 123)


@pytest.mark.asyncio
async def test_dispatch_issue_remediator_refused_when_open_pr_references_issue(
    mock_runner: StaffRunner, test_principal: Principal
) -> None:
    """When an open PR exists referencing the target issue, 409 Conflict is raised."""
    cmd = DispatchCommand(
        role="issue-remediator",
        requested_by="test:operator",
        repo="Test_Repo",
        issue=123,
        prompt="Fix the bug",
    )

    open_prs = [
        {
            "number": 456,
            "title": "fix: resolve issue #123",
            "html_url": "https://github.com/D-sorganization/Test_Repo/pull/456",
            "head": {"ref": "fix/123-bug"},
            "body": "Fixes #123",
        }
    ]

    with (
        patch("staff.dispatch_service.get_runner", return_value=mock_runner),
        patch("staff.preflight_duplicate_guard.get_runner", return_value=mock_runner),
        patch("gh_utils.gh_api", new_callable=AsyncMock, return_value=open_prs),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await dispatch_staff_run(cmd, test_principal)

        assert exc_info.value.status_code == 409
        detail = exc_info.value.detail
        assert detail["code"] == "open_pr_exists"
        assert "pr-remediator" in detail["message"]
        assert "456" in detail["message"]


@pytest.mark.asyncio
async def test_dispatch_issue_remediator_refused_when_active_run_targets_issue(
    mock_runner: StaffRunner, test_principal: Principal
) -> None:
    """When an active in-flight staff run already targets (repo, issue), 409 Conflict is raised."""
    mock_runner.store.create_run(
        RunRecord(
            id="run-existing-123",
            role="issue-remediator",
            provider="claude",
            model="sonnet",
            machine="local",
            repo="Test_Repo",
            target_kind="issue",
            target_ref="123",
            prompt="Working on issue #123",
            status="running",
        )
    )

    cmd = DispatchCommand(
        role="issue-remediator",
        requested_by="test:operator",
        repo="Test_Repo",
        issue=123,
        prompt="Fix the bug",
    )

    with (
        patch("staff.dispatch_service.get_runner", return_value=mock_runner),
        patch("staff.preflight_duplicate_guard.get_runner", return_value=mock_runner),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await dispatch_staff_run(cmd, test_principal)

        assert exc_info.value.status_code == 409
        detail = exc_info.value.detail
        assert detail["code"] == "active_run_exists"
        assert "run-existing-123" in detail["message"]


@pytest.mark.asyncio
async def test_dispatch_force_duplicate_bypasses_guard(mock_runner: StaffRunner, test_principal: Principal) -> None:
    """When force_duplicate=True is set, dispatch is allowed even if an active run exists."""
    mock_runner.store.create_run(
        RunRecord(
            id="run-existing-123",
            role="issue-remediator",
            provider="claude",
            model="sonnet",
            machine="local",
            repo="Test_Repo",
            target_kind="issue",
            target_ref="123",
            prompt="Working on issue #123",
            status="running",
        )
    )

    cmd = DispatchCommand(
        role="issue-remediator",
        requested_by="test:operator",
        repo="Test_Repo",
        issue=123,
        prompt="Fix the bug",
        force_duplicate=True,
        dry_run=True,
    )

    with (
        patch("staff.dispatch_service.get_runner", return_value=mock_runner),
        patch("staff.preflight_duplicate_guard.get_runner", return_value=mock_runner),
    ):
        res = await dispatch_staff_run(cmd, test_principal)
        assert res is not None
        assert res.get("dry_run") is True


@pytest.mark.asyncio
async def test_check_duplicate_pr_or_run_direct_call(mock_runner: StaffRunner) -> None:
    with patch("staff.preflight_duplicate_guard.get_runner", return_value=mock_runner):
        # Clean call with no active run and no open PRs
        await check_duplicate_pr_or_run("Test_Repo", 999, gh_get_fn=AsyncMock(return_value=[]))


@pytest.mark.asyncio
async def test_check_duplicate_pr_or_run_refuses_on_merged_pr(mock_runner: StaffRunner) -> None:
    async def gh_get(endpoint: str):
        if "pulls?state=open" in endpoint:
            return []
        if "pulls?state=closed" in endpoint:
            return [
                {
                    "number": 778,
                    "title": "fix: complete issue #123",
                    "body": "Closes #123",
                    "merged_at": "2026-10-24T00:00:00Z",
                    "head": {"ref": "staff/issue-remediator-123-a1b2c3"},
                    "html_url": "https://example.invalid/pr/778",
                }
            ]
        if "branches?per_page=100" in endpoint:
            return []
        if "issues/123" in endpoint:
            return {"labels": []}
        return []

    with patch("staff.preflight_duplicate_guard.get_runner", return_value=mock_runner):
        with pytest.raises(HTTPException) as exc_info:
            await check_duplicate_pr_or_run("Test_Repo", 123, gh_get_fn=gh_get)
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "merged_pr_exists"
        assert "778" in exc_info.value.detail["message"]


@pytest.mark.asyncio
async def test_check_duplicate_pr_or_run_refuses_on_issue_branch(mock_runner: StaffRunner) -> None:
    async def gh_get(endpoint: str):
        if "pulls?state=open" in endpoint:
            return []
        if "pulls?state=closed" in endpoint:
            return []
        if "branches?per_page=100" in endpoint:
            return [{"name": "staff/issue-remediator-123-deadbe"}]
        if "issues/123" in endpoint:
            return {"labels": []}
        return []

    with patch("staff.preflight_duplicate_guard.get_runner", return_value=mock_runner):
        with pytest.raises(HTTPException) as exc_info:
            await check_duplicate_pr_or_run("Test_Repo", 123, gh_get_fn=gh_get)
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "issue_branch_exists"
        assert "issue-remediator-123" in exc_info.value.detail["message"]


@pytest.mark.asyncio
async def test_check_duplicate_pr_or_run_refuses_on_active_claim_label(mock_runner: StaffRunner) -> None:
    async def gh_get(endpoint: str):
        if "pulls?state=open" in endpoint:
            return []
        if "pulls?state=closed" in endpoint:
            return []
        if "branches?per_page=100" in endpoint:
            return []
        if "issues/123" in endpoint:
            return {"labels": [{"name": "claim:cursor-agent"}]}
        return []

    with patch("staff.preflight_duplicate_guard.get_runner", return_value=mock_runner):
        with pytest.raises(HTTPException) as exc_info:
            await check_duplicate_pr_or_run("Test_Repo", 123, gh_get_fn=gh_get)
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "active_claim_exists"
        assert "claim:cursor-agent" in exc_info.value.detail["message"]
