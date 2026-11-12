"""Regression tests for #4260: premise marker, exact match, no auto-close, allowlist only."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from dispatch_premise import (
    _check_runs_on_main,
    close_item_as_resolved_on_main,
    evaluate_dispatch_premise,
    extract_failing_check_premise,
    recheck_premise_on_main,
)


def test_reviewer_prompts_without_marker_produce_no_premise() -> None:
    """Free-text .yml / LoD mentions must not imply a failing-check premise (#4260 I-01)."""
    assert extract_failing_check_premise("Fix failing workflow local-only-runner-guard.yml on AffineDrift") is None
    assert extract_failing_check_premise("Fix Law of Demeter violations: check_law_of_demeter baseline") is None


def test_structured_failing_check_marker_required() -> None:
    info = extract_failing_check_premise("failing-check: local-only-runner-guard\nDo the fix.")
    assert info is not None
    assert info["check_name"] == "local-only-runner-guard"
    assert info["command"] == ""


def test_prompt_derived_pytest_command_not_extracted() -> None:
    assert extract_failing_check_premise("pytest tests/unit/test_metrics.py failed with exit code 1") is None


@pytest.mark.asyncio
async def test_exact_check_name_match_lint_not_lint_docs() -> None:
    async def mock_gh_get(endpoint: str) -> dict:
        return {
            "check_runs": [
                {"name": "lint-docs", "status": "completed", "conclusion": "success", "head_sha": "abc1234"},
                {"name": "lint", "status": "completed", "conclusion": "failure", "head_sha": "abc1234"},
            ],
        }

    passed, _sha = await _check_runs_on_main("D-sorganization/Runner_Dashboard", "lint", mock_gh_get)
    assert passed is False


@pytest.mark.asyncio
async def test_recheck_never_runs_prompt_derived_command() -> None:
    run_calls: list[list[str]] = []

    async def mock_run_cmd(cmd: list[str], **kwargs) -> tuple[int, str, str]:
        run_calls.append(list(cmd))
        return 0, "", ""

    check_info = {
        "check_name": "check_law_of_demeter",
        "check_type": "script",
        "command": "pytest tests/evil_injected.py",
    }
    async def mock_gh_get(_endpoint: str) -> dict:
        return {}

    await recheck_premise_on_main(
        "D-sorganization/Runner_Dashboard",
        check_info,
        gh_get_fn=mock_gh_get,
        run_cmd_fn=mock_run_cmd,
    )
    assert run_calls == [["python3", "scripts/ci/check_law_of_demeter.py"]]
    assert not any("evil_injected" in " ".join(call) for call in run_calls)


@pytest.mark.asyncio
async def test_already_resolved_on_main_never_sets_should_close() -> None:
    async def mock_gh_get(endpoint: str) -> dict | list:
        if "check-runs" in endpoint:
            return {
                "check_runs": [
                    {
                        "name": "local-only-runner-guard",
                        "status": "completed",
                        "conclusion": "success",
                        "head_sha": "d354634",
                    }
                ],
            }
        if "pulls" in endpoint:
            return []
        if "issues/" in endpoint:
            return {"number": 100, "labels": []}
        return {}

    res = await evaluate_dispatch_premise(
        repository="D-sorganization/AffineDrift",
        issue_number=100,
        prompt="failing-check: local-only-runner-guard",
        gh_get_fn=mock_gh_get,
    )
    assert res.allowed is False
    assert res.reason == "already_resolved_on_main"
    assert res.should_close is False


def test_dispatch_premise_module_never_closes_issues() -> None:
    """No gh issue close subprocess in dispatch_premise (static guard)."""
    source = (Path(__file__).parent.parent / "backend" / "dispatch_premise.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    close_literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == "close":
            close_literals.append(node.value)
    assert close_literals == []
    assert "close_cmd" not in source


@pytest.mark.asyncio
async def test_close_item_as_resolved_on_main_comment_only() -> None:
    run_calls: list[list[str]] = []

    async def mock_run_cmd(cmd: list[str], **kwargs) -> tuple[int, str, str]:
        run_calls.append(cmd)
        return 0, "", ""

    ok = await close_item_as_resolved_on_main(
        "D-sorganization/Runner_Dashboard",
        42,
        comment="already resolved on main at d354634",
        run_cmd_fn=mock_run_cmd,
    )
    assert ok is True
    assert len(run_calls) == 1
    assert run_calls[0][:3] == ["gh", "issue", "comment"]
    assert "close" not in run_calls[0]
