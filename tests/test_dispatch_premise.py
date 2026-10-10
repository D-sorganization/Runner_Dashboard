"""Unit tests for pre-dispatch premise checking (Issue #1847 / RD-2).

Verifies:
1. Tasks naming a failing check or command are re-checked against current main.
   If passing on main: comment "already resolved on main at <sha>", close, skip dispatch.
   If failing on main: dispatch proceeds normally.
2. If an open PR references the issue, dispatch is skipped.
3. If an active claim:* lease exists, dispatch is skipped.
4. Every skip is recorded with its reason in the dispatch audit log.
5. Replay against the three audited no-op prompts skips all three.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure backend is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from dispatch_premise import (
    AUDITED_NO_OP_PROMPTS,
    PremiseCheckResult,
    check_issue_has_active_claim,
    check_issue_has_open_pr,
    close_item_as_resolved_on_main,
    evaluate_dispatch_premise,
    extract_failing_check_premise,
    log_dispatch_skip,
)


@pytest.mark.asyncio
async def test_extract_failing_check_premise():
    """Verify check names, commands, and patterns are extracted from prompts."""
    # Audited prompt 1: LoD baseline
    p1 = extract_failing_check_premise("Fix Law of Demeter violations: check_law_of_demeter")
    assert p1 is not None
    assert "demeter" in p1["check_name"].lower()

    # Audited prompt 2: Stray conflict marker
    p2 = extract_failing_check_premise("Remove stray conflict marker <<<<<<< from docs/HANDOFF.md")
    assert p2 is not None
    assert p2["check_type"] == "conflict_marker"

    # Audited prompt 3: local-only-runner-guard
    p3 = extract_failing_check_premise("Fix failing workflow local-only-runner-guard.yml on AffineDrift")
    assert p3 is not None
    assert "local-only-runner-guard" in p3["check_name"]

    # Command prompt
    p4 = extract_failing_check_premise("pytest tests/unit/test_metrics.py failed with exit code 1")
    assert p4 is not None
    assert "pytest" in p4["command"]

    # Generic prompt with no failing check
    p_none = extract_failing_check_premise("Add new button to user profile page")
    assert p_none is None


@pytest.mark.asyncio
async def test_open_pr_skips_dispatch():
    """If an open PR references the issue, dispatch is skipped."""

    async def mock_gh_get(endpoint: str) -> list[dict]:
        if "pulls" in endpoint:
            return [
                {
                    "number": 1901,
                    "title": "fix(core): resolve issue #42",
                    "body": "Closes #42\nFixes the memory leak",
                    "head": {"ref": "fix-issue-42"},
                }
            ]
        return []

    has_pr, pr_info = await check_issue_has_open_pr("D-sorganization/Runner_Dashboard", 42, gh_get_fn=mock_gh_get)
    assert has_pr is True
    assert pr_info is not None
    assert pr_info["number"] == 1901

    res = await evaluate_dispatch_premise(
        repository="D-sorganization/Runner_Dashboard",
        issue_number=42,
        prompt="Fix the memory leak",
        gh_get_fn=mock_gh_get,
    )
    assert res.allowed is False
    assert res.reason == "open_pr_exists"
    assert "1901" in res.detail


@pytest.mark.asyncio
async def test_active_claim_lease_skips_dispatch():
    """If an active claim:* lease or label exists, dispatch is skipped."""

    async def mock_gh_get(endpoint: str) -> dict:
        if "issues/42" in endpoint:
            return {
                "number": 42,
                "labels": [{"name": "claim:antigravity"}, {"name": "tier:cli"}],
            }
        return {}

    has_claim, reason = await check_issue_has_active_claim(
        "D-sorganization/Runner_Dashboard", 42, gh_get_fn=mock_gh_get
    )
    assert has_claim is True
    assert "claim:antigravity" in reason

    res = await evaluate_dispatch_premise(
        repository="D-sorganization/Runner_Dashboard",
        issue_number=42,
        prompt="Fix something",
        gh_get_fn=mock_gh_get,
    )
    assert res.allowed is False
    assert res.reason == "active_claim_exists"
    assert "claim:antigravity" in res.detail


@pytest.mark.asyncio
async def test_check_passes_on_main_skips_and_comments():
    """If the failing check passes on current main, comment and skip dispatch."""

    async def mock_gh_get(endpoint: str) -> dict:
        if "commits/main/check-runs" in endpoint or "check-runs" in endpoint:
            return {
                "total_count": 1,
                "check_runs": [
                    {
                        "name": "local-only-runner-guard",
                        "status": "completed",
                        "conclusion": "success",
                        "head_sha": "d354634",
                    }
                ],
            }
        if "issues/100" in endpoint:
            return {"number": 100, "labels": []}
        return {}

    res = await evaluate_dispatch_premise(
        repository="D-sorganization/AffineDrift",
        issue_number=100,
        prompt="Fix local-only-runner-guard failure in D-sorganization/AffineDrift",
        gh_get_fn=mock_gh_get,
    )
    assert res.allowed is False
    assert res.reason == "already_resolved_on_main"
    assert "already resolved on main at" in res.comment
    assert res.should_close is True


@pytest.mark.asyncio
async def test_check_fails_on_main_allows_dispatch():
    """If the failing check still fails on main, dispatch is allowed."""

    async def mock_gh_get(endpoint: str) -> dict:
        if "commits/main/check-runs" in endpoint or "check-runs" in endpoint:
            return {
                "total_count": 1,
                "check_runs": [
                    {
                        "name": "local-only-runner-guard",
                        "status": "completed",
                        "conclusion": "failure",
                        "head_sha": "d354634",
                    }
                ],
            }
        if "issues/100" in endpoint:
            return {"number": 100, "labels": []}
        if "pulls" in endpoint:
            return []
        return {}

    res = await evaluate_dispatch_premise(
        repository="D-sorganization/AffineDrift",
        issue_number=100,
        prompt="Fix local-only-runner-guard failure in D-sorganization/AffineDrift",
        gh_get_fn=mock_gh_get,
    )
    assert res.allowed is True
    assert res.reason == ""


@pytest.mark.asyncio
async def test_audited_three_no_op_prompts_replayed_skip_all():
    """Acceptance: Replayed against the three audited no-op prompts, the check skips all three."""

    # Mock environment where the three premises have already been resolved on main
    async def mock_gh_get(endpoint: str) -> dict | list:
        if "pulls" in endpoint:
            return []
        if "check-runs" in endpoint:
            return {
                "check_runs": [
                    {
                        "name": "local-only-runner-guard",
                        "conclusion": "success",
                        "status": "completed",
                        "head_sha": "d354634",
                    },
                    {
                        "name": "check_law_of_demeter",
                        "conclusion": "success",
                        "status": "completed",
                        "head_sha": "d354634",
                    },
                ]
            }
        if "commits/main" in endpoint:
            return {"sha": "d354634"}
        return {}

    async def mock_run_cmd(cmd: list[str], **kwargs) -> tuple[int, str, str]:
        # Local command checks return 0 (passing on main)
        return 0, "", ""

    # Replay Prompt 1: LoD baseline already fixed on branch/main
    p1 = AUDITED_NO_OP_PROMPTS[0]
    res1 = await evaluate_dispatch_premise(
        repository=p1["repository"],
        issue_number=p1.get("issue"),
        prompt=p1["prompt"],
        gh_get_fn=mock_gh_get,
        run_cmd_fn=mock_run_cmd,
    )
    assert res1.allowed is False, f"Prompt 1 did not skip: {res1}"
    assert res1.reason == "already_resolved_on_main"

    # Replay Prompt 2: Stray conflict marker already removed by #1739
    p2 = AUDITED_NO_OP_PROMPTS[1]
    res2 = await evaluate_dispatch_premise(
        repository=p2["repository"],
        issue_number=p2.get("issue"),
        prompt=p2["prompt"],
        gh_get_fn=mock_gh_get,
        run_cmd_fn=mock_run_cmd,
    )
    assert res2.allowed is False, f"Prompt 2 did not skip: {res2}"
    assert res2.reason == "already_resolved_on_main"

    # Replay Prompt 3: AffineDrift local-only-runner-guard fixed 7 weeks earlier
    p3 = AUDITED_NO_OP_PROMPTS[2]
    res3 = await evaluate_dispatch_premise(
        repository=p3["repository"],
        issue_number=p3.get("issue"),
        prompt=p3["prompt"],
        gh_get_fn=mock_gh_get,
        run_cmd_fn=mock_run_cmd,
    )
    assert res3.allowed is False, f"Prompt 3 did not skip: {res3}"
    assert res3.reason == "already_resolved_on_main"


@pytest.mark.asyncio
async def test_dispatch_audit_logging_on_skip():
    """Verify dispatch audit log receives the skip record."""
    audit_calls: list[dict] = []

    async def mock_audit(entry: dict) -> None:
        audit_calls.append(entry)

    res = PremiseCheckResult(
        allowed=False,
        reason="already_resolved_on_main",
        detail="check passed on main",
        comment="already resolved on main at d354634",
        should_close=True,
        head_sha="d354634",
    )
    await log_dispatch_skip(res, "D-sorganization/Runner_Dashboard", 1847, audit_fn=mock_audit)

    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "dispatch.premise_check"
    assert audit_calls[0]["decision"] == "skipped"
    assert audit_calls[0]["reason"] == "already_resolved_on_main"
    assert audit_calls[0]["target"] == "D-sorganization/Runner_Dashboard#1847"


@pytest.mark.asyncio
async def test_close_item_as_resolved_on_main():
    """Verify issue comment and close invocation."""
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
    assert len(run_calls) == 2
    # First call: comment
    assert "comment" in run_calls[0] or "comments" in "".join(run_calls[0])
    # Second call: close issue
    assert "close" in run_calls[1] or "closed" in "".join(run_calls[1])
