"""Regression tests for UpstreamDrift #15555 merge shepherd retry suppression replay (#4658).

Reproduces the UD #15555 sequence:
- Repeated identical deterministic merge_group failures on unchanged head
- Manual dequeue comments/events
- Owner-directed unarmed note / do-not-merge
- Nearby clean eligible PR as control
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from coordination.merge_shepherd import (
    IneligibleReason,
    PRCandidate,
    ShepherdAction,
    evaluate_pr_shepherd_eligibility,
    has_deterministic_failure_or_unauthorized_requeue,
)
from coordination.merge_shepherd_pass import plan_repo_pass

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "ud_15555_merge_shepherd_replay.json"


@pytest.fixture
def replay_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fixture_structure(replay_fixture: dict[str, Any]) -> None:
    prs = replay_fixture["ud_15555_replay"]["repository"]["pullRequests"]["nodes"]
    assert len(prs) == 2
    assert prs[0]["number"] == 15555
    assert prs[1]["number"] == 15556


def test_has_deterministic_failure_or_unauthorized_requeue_detection(replay_fixture: dict[str, Any]) -> None:
    node_15555 = replay_fixture["ud_15555_replay"]["repository"]["pullRequests"]["nodes"][0]
    head = node_15555["headRefOid"]

    suppressed, reason = has_deterministic_failure_or_unauthorized_requeue(
        node_15555.get("timelineItems", {}).get("nodes", []),
        node_15555.get("comments", {}).get("nodes", []),
        head_sha=head,
        head_committed_at="2026-11-23T05:50:00Z",
    )
    assert suppressed is True
    assert reason in {
        "manual_dequeue_without_authorization",
        "deterministic_failure_unchanged_head",
        "unarmed_owner_direction",
    }


def test_clean_control_pr_is_not_suppressed(replay_fixture: dict[str, Any]) -> None:
    node_15556 = replay_fixture["ud_15555_replay"]["repository"]["pullRequests"]["nodes"][1]
    head = node_15556["headRefOid"]

    suppressed, reason = has_deterministic_failure_or_unauthorized_requeue(
        node_15556.get("timelineItems", {}).get("nodes", []),
        node_15556.get("comments", {}).get("nodes", []),
        head_sha=head,
        head_committed_at="2026-11-24T20:00:00Z",
    )
    assert suppressed is False
    assert reason == ""


def test_plan_repo_pass_with_ud_15555_replay(replay_fixture: dict[str, Any]) -> None:
    nodes = replay_fixture["ud_15555_replay"]["repository"]["pullRequests"]["nodes"]

    actions, skips, backoff = plan_repo_pass(
        "D-sorganization/UpstreamDrift",
        nodes,
        skip_list=frozenset(),
        holds=(),
        armed_count=0,
        queue_depth=0,
        live=False,
    )

    assert backoff is False
    # Clean control PR #15556 must be armed
    armed_prs = [a.pr_number for a in actions]
    assert armed_prs == [15556]

    # PR #15555 must be skipped (either held, bounce_limit, or retry_suppressed)
    skip_prs = [s["pr"] for s in skips]
    assert 15555 in skip_prs
    assert 15556 not in skip_prs


def test_retry_suppressed_candidate_evaluates_to_skip() -> None:
    candidate = PRCandidate(
        repo="D-sorganization/UpstreamDrift",
        pr_number=15555,
        head_sha="1111111111111111111111111111111111111111",
        retry_suppressed=True,
        retry_suppressed_detail="manual_dequeue_without_authorization",
    )
    decision = evaluate_pr_shepherd_eligibility(candidate)
    assert decision.action == ShepherdAction.SKIP
    assert decision.reason == IneligibleReason.RETRY_SUPPRESSED
    assert decision.detail == "manual_dequeue_without_authorization"


def test_explicit_retry_authorization_clears_suppression() -> None:
    timeline = [{"createdAt": "2026-11-24T15:07:31Z", "reason": "dequeued"}]
    comments = [
        {
            "author": {"login": "d-sorggeminiagent"},
            "createdAt": "2026-11-24T15:08:00Z",
            "body": "Dequeued and disarmed #15555",
        },
        {
            "author": {"login": "dieterolson"},
            "createdAt": "2026-11-24T15:09:00Z",
            "body": "Authorized retry on same head: verified transient failure.",
        },
    ]
    suppressed, reason = has_deterministic_failure_or_unauthorized_requeue(
        timeline,
        comments,
        head_sha="1111111",
        head_committed_at="2026-11-24T14:00:00Z",
    )
    assert suppressed is False
    assert reason == ""
