"""#4249 B-04: the merge shepherd counts queue ejections and enforces the bounce limit."""

from __future__ import annotations

from typing import Any

import pytest
from coordination.merge_shepherd import BOUNCE_LIMIT, count_queue_bounces
from coordination.merge_shepherd_exact_head import HeadReviewPass
from coordination.merge_shepherd_pass import OPEN_PRS_QUERY, node_to_candidate, plan_repo_pass

from tests.unit.test_merge_shepherd_pass import _open_pr_node, _review_pass

REPO = "D-sorganization/Runner_Dashboard"
HEAD_AT = "2026-11-14T21:35:50Z"
EARLIER = "2026-11-14T20:00:00Z"
LATER_1 = "2026-11-15T01:12:40Z"
LATER_2 = "2026-11-15T01:46:33Z"


def _ejection(at: str, reason: str = "failed_checks") -> dict[str, str]:
    return {"createdAt": at, "reason": reason}


def _node(number: int, ejections: list[dict[str, str]], *, head_at: str = HEAD_AT) -> dict[str, Any]:
    node = _open_pr_node(number)
    node["commits"]["nodes"][0]["commit"]["committedDate"] = head_at
    node["timelineItems"] = {"nodes": ejections}
    return node


def _plan(nodes: list[dict[str, Any]], passes: list[HeadReviewPass]) -> tuple[list[Any], list[dict[str, Any]]]:
    actions, skips, backoff = plan_repo_pass(
        REPO,
        nodes,
        skip_list=frozenset(),
        holds=(),
        armed_count=0,
        queue_depth=0,
        live=False,
        review_passes=passes,
    )
    assert backoff is False
    return actions, skips


def test_the_query_asks_for_queue_ejections_and_the_head_commit_time() -> None:
    assert "REMOVED_FROM_MERGE_QUEUE_EVENT" in OPEN_PRS_QUERY
    assert "committedDate" in OPEN_PRS_QUERY


def test_ejections_since_the_head_commit_are_bounces() -> None:
    assert count_queue_bounces(_node(1, [_ejection(LATER_1), _ejection(LATER_2)])) == 2


def test_an_ejection_before_the_head_commit_is_not_a_bounce_of_this_head() -> None:
    assert count_queue_bounces(_node(1, [_ejection(EARLIER), _ejection(LATER_1)])) == 1


@pytest.mark.parametrize("reason", ["merged", "MERGED", "queue_cleared", "roll_back"])
def test_an_ejection_that_is_not_the_prs_failure_is_not_a_bounce(reason: str) -> None:
    assert count_queue_bounces(_node(1, [_ejection(LATER_1, reason)])) == 0


def test_an_ejection_with_an_unknown_reason_counts() -> None:
    assert count_queue_bounces(_node(1, [_ejection(LATER_1, "something_new")])) == 1


def test_every_ejection_counts_when_the_head_commit_time_is_unknown() -> None:
    assert count_queue_bounces(_node(1, [_ejection(EARLIER), _ejection(LATER_1)], head_at="")) == 2


@pytest.mark.parametrize("timeline", [None, {}, {"nodes": None}, {"nodes": [None, {}]}])
def test_a_node_without_ejections_has_no_bounces(timeline: object) -> None:
    node = _open_pr_node(1)
    node["timelineItems"] = timeline
    assert count_queue_bounces(node) == 0


def test_the_candidate_carries_the_bounce_count() -> None:
    candidate = node_to_candidate(REPO, _node(7, [_ejection(LATER_1), _ejection(LATER_2)]), is_drained=False)
    assert candidate.bounces == 2


def test_a_pr_at_the_bounce_limit_is_not_armed_again() -> None:
    bounced = _node(100, [_ejection(LATER_1)] * BOUNCE_LIMIT)
    actions, skips = _plan([bounced], [_review_pass(100, "sha100")])
    assert actions == []
    assert {"repo": REPO, "pr": 100, "reason": "bounce_limit"} in skips


def test_a_pr_below_the_bounce_limit_is_still_armed() -> None:
    once = _node(101, [_ejection(LATER_1)] * (BOUNCE_LIMIT - 1))
    actions, _ = _plan([once], [_review_pass(101, "sha101")])
    assert [action.pr_number for action in actions] == [101]


def test_a_new_head_commit_clears_the_bounces() -> None:
    pushed = _node(102, [_ejection(LATER_1), _ejection(LATER_2)], head_at="2026-11-15T03:00:00Z")
    actions, _ = _plan([pushed], [_review_pass(102, "sha102")])
    assert [action.pr_number for action in actions] == [102]
