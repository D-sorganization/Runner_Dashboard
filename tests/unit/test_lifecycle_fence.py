"""Operator PR-lifecycle fence: restrictions outrank computed defaults (RM#2712)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from routers.staff import RunBody
from staff.lifecycle_fence import (
    FENCED_LIFECYCLE,
    LifecycleContractError,
    assert_fence_consumed,
    resolve_pr_lifecycle,
)
from staff.plan import RunRequest
from staff.roles import RoleSpec
from staff.runner import StaffRunner
from staff.store import RunStore


@pytest.mark.unit
def test_unrestricted_request_keeps_ordinary_lifecycle() -> None:
    decision = resolve_pr_lifecycle("arm_and_exit", ())
    assert decision.pr_lifecycle == "arm_and_exit"
    assert decision.prompt_block == ""
    assert not decision.fenced


@pytest.mark.unit
def test_operator_restriction_outranks_default_arm_and_exit() -> None:
    decision = resolve_pr_lifecycle("arm_and_exit", ("no_arm", "draft_only"))
    assert decision.pr_lifecycle == FENCED_LIFECYCLE
    assert decision.restrictions == ("draft_only", "no_arm")
    assert "draft" in decision.prompt_block.lower()
    assert "arm_and_exit" not in decision.prompt_block


@pytest.mark.unit
@pytest.mark.parametrize("restrictions", [("allow_arm",), ("draft_only", ""), ("NO_ARM",)])
def test_unknown_restriction_fails_closed(restrictions: tuple[str, ...]) -> None:
    with pytest.raises(LifecycleContractError, match="restriction"):
        resolve_pr_lifecycle("arm_and_exit", restrictions)


@pytest.mark.unit
@pytest.mark.parametrize("requested", ["subscribed", "", "ship_it"])
def test_restriction_with_contradictory_lifecycle_fails_closed(requested: str) -> None:
    with pytest.raises(LifecycleContractError, match="contradicts"):
        resolve_pr_lifecycle(requested, ("no_arm",))


@pytest.mark.unit
def test_fenced_prompt_missing_block_fails_closed() -> None:
    decision = resolve_pr_lifecycle("arm_and_exit", ("no_arm",))
    with pytest.raises(LifecycleContractError, match="not consumed"):
        assert_fence_consumed(decision, "open ready PRs only (pr_lifecycle: arm_and_exit)")
    # The fence must be the final word so it overrides earlier standards text.
    with pytest.raises(LifecycleContractError, match="not consumed"):
        assert_fence_consumed(decision, f"{decision.prompt_block}\n\nlater: arm auto-merge")
    assert_fence_consumed(decision, f"standards\n\n{decision.prompt_block}")
    assert_fence_consumed(resolve_pr_lifecycle("arm_and_exit", ()), "anything")


def _runner(tmp_path: Path) -> StaffRunner:
    role = RoleSpec(
        name="issue-remediator",
        title="Issue Remediator",
        providers=("claude",),
        permissions={"push_branch": True, "open_pr": True},
        surface="dashboard",
    )
    return StaffRunner(store=RunStore(tmp_path / "runs.sqlite3"), roles_loader=lambda: {role.name: role})


@pytest.mark.unit
def test_runtime_plan_consumes_fence(tmp_path: Path, staff_cli_installed) -> None:
    staff_cli_installed("claude")
    plan = _runner(tmp_path).plan(
        RunRequest(role="issue-remediator", prompt="fix it", repo="Repository_Management", pr_restrictions=("no_arm",))
    )
    decision = resolve_pr_lifecycle("arm_and_exit", ("no_arm",))
    assert plan.pr_lifecycle == FENCED_LIFECYCLE
    assert plan.to_dict()["pr_lifecycle"] == FENCED_LIFECYCLE
    assert plan.prompt.endswith(decision.prompt_block)
    assert any(decision.prompt_block in arg for arg in plan.argv)


@pytest.mark.unit
def test_runtime_plan_unrestricted_is_unchanged(tmp_path: Path, staff_cli_installed) -> None:
    staff_cli_installed("claude")
    plan = _runner(tmp_path).plan(RunRequest(role="issue-remediator", prompt="fix it", repo="Repository_Management"))
    assert plan.pr_lifecycle == "arm_and_exit"
    assert "OPERATOR PR BOUNDARY" not in plan.prompt


@pytest.mark.unit
def test_runtime_plan_refuses_contradictory_contract(tmp_path: Path, staff_cli_installed) -> None:
    staff_cli_installed("claude")
    req = RunRequest(
        role="issue-remediator",
        prompt="fix it",
        repo="Repository_Management",
        pr_lifecycle="subscribed",
        pr_restrictions=("draft_only",),
    )
    with pytest.raises(ValueError, match="contradicts"):
        _runner(tmp_path).plan(req)


@pytest.mark.unit
def test_run_body_carries_only_known_restrictions() -> None:
    assert RunBody(pr_restrictions=["draft_only", "no_arm"]).pr_restrictions == ["draft_only", "no_arm"]
    assert RunBody().pr_restrictions == []
    with pytest.raises(ValidationError):
        RunBody(pr_restrictions=["allow_arm"])


@pytest.mark.unit
def test_builtin_standards_defer_to_pr_restrictions_override() -> None:
    """The standards text itself must not contradict the fence (#4090, follow-up to RM#2712/#4088)."""
    from staff.standards_registry import _BUILTIN

    body = _BUILTIN.body
    assert "pr_restrictions" in body
    assert FENCED_LIFECYCLE in body
    assert "unless" in body.lower()


@pytest.mark.unit
def test_runtime_plan_fenced_prompt_standards_text_has_no_unconditional_arm_line(
    tmp_path: Path, staff_cli_installed
) -> None:
    """A fenced prompt's standards block must defer to the fence, not assert an unconditional arm."""
    staff_cli_installed("claude")
    plan = _runner(tmp_path).plan(
        RunRequest(role="issue-remediator", prompt="fix it", repo="Repository_Management", pr_restrictions=("no_arm",))
    )
    standards_section = plan.prompt.split("OPERATOR PR BOUNDARY")[0]
    assert "pr_restrictions" in standards_section
    assert "unless" in standards_section.lower()
    assert plan.prompt.endswith(resolve_pr_lifecycle("arm_and_exit", ("no_arm",)).prompt_block)
