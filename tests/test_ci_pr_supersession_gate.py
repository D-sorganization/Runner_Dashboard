"""Behaviour tests for PR superseded-cancel neutrality (issue #2764 / PR #2884)."""

from __future__ import annotations

import pytest

from shared_scripts.ci_pr_supersession_gate import pr_cancel_is_superseded_neutral

SHA_A = "abc1234567890abcdef0123456789abcdef0123456"  # pragma: allowlist secret
SHA_B = "def0123456789abcdef0123456789abcdef01234567"  # pragma: allowlist secret


@pytest.mark.parametrize(
    ("event", "result", "run_sha", "pr_sha", "neutral"),
    [
        ("pull_request", "cancelled", SHA_A, SHA_B, True),
        ("pull_request", "cancelled", SHA_A, SHA_A, False),
        ("pull_request", "failure", SHA_A, SHA_B, False),
        ("pull_request", "cancelled", "", SHA_B, False),
        ("merge_group", "cancelled", SHA_A, SHA_B, False),
    ],
)
def test_pr_cancel_neutral_only_when_head_stale(
    event: str, result: str, run_sha: str, pr_sha: str, neutral: bool
) -> None:
    assert (
        pr_cancel_is_superseded_neutral(
            event_name=event,
            job_result=result,
            run_head_sha=run_sha,
            pr_head_sha=pr_sha,
        )
        is neutral
    )
