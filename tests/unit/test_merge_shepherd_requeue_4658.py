"""Offline replay: shepherd must not requeue UD #15555-style deterministic failures."""

from __future__ import annotations

from coordination.merge_shepherd_pass import plan_repo_disarms, plan_repo_pass

from tests.unit.test_merge_shepherd_pass import _open_pr_node, _review_pass

REPO = "D-sorganization/UpstreamDrift"
FAILING_PR = 15555
CONTROL_PR = 15556
HEAD = "a" * 40
CONTROL_HEAD = "b" * 40
HEAD_AT = "2026-11-24T21:50:00Z"
SIGNATURE = "stale-generated-typescript+hash-seed"
RUN_IDS = (45496778157, 45502515157, 45502515158, 45502515159)


def _removal(at: str, reason: str) -> dict[str, str]:
    return {"createdAt": at, "reason": reason}


def _run(
    run_id: int, *, classification: str = "deterministic", signature: str = SIGNATURE, head: str = HEAD
) -> dict[str, object]:
    return {
        "headOid": head,
        "databaseId": run_id,
        "conclusion": "failure",
        "classification": classification,
        "signature": signature,
    }


def _with_head(node: dict, head: str, committed_at: str) -> dict:
    node["headRefOid"] = head
    node["commits"]["nodes"][0]["commit"]["committedDate"] = committed_at
    return node


def _ud_15555_node() -> dict:
    """Armed PR ejected on one head, then manually dequeued, with an unarmed note."""
    node = _with_head(_open_pr_node(FAILING_PR, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["autoMergeRequest"] = {"enabledAt": "2026-11-24T21:40:00Z"}
    node["timelineItems"] = {
        "nodes": [
            _removal("2026-11-24T22:05:00Z", "CI_FAILURE"),
            _removal("2026-11-24T22:28:00Z", "CI_FAILURE"),
            _removal("2026-11-24T22:51:00Z", "CI_FAILURE"),
            _removal("2026-11-24T23:03:40Z", "CI_FAILURE"),
            _removal("2026-11-24T23:07:31Z", "MANUAL"),
        ]
    }
    node["mergeGroupRuns"] = [
        _run(RUN_IDS[0]),
        _run(RUN_IDS[1]),
        _run(RUN_IDS[2]),
        _run(RUN_IDS[3]),
    ]
    node["comments"] = {
        "nodes": [
            {
                "createdAt": "2026-11-24T23:10:36Z",
                "author": {"login": "dieterolson"},
                "body": "owner-directed unarmed note; do not requeue this head",
            }
        ]
    }
    return node


def _control_node() -> dict:
    return _with_head(_open_pr_node(CONTROL_PR, paths=["docs/readme.md"]), CONTROL_HEAD, HEAD_AT)


def _plan(nodes: list[dict]) -> tuple[list, list]:
    passes = [_review_pass(node["number"], node["headRefOid"]) for node in nodes]
    arms, _, backoff = plan_repo_pass(
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
    disarms = plan_repo_disarms(
        REPO,
        nodes,
        skip_list=frozenset(),
        holds=(),
        live=False,
        review_passes=passes,
    )
    return arms, disarms


def test_ud_15555_replay_suppresses_requeue_and_still_arms_the_clean_control() -> None:
    arms, disarms = _plan([_ud_15555_node(), _control_node()])
    armed = {action.pr_number for action in arms}
    disarmed = {action.pr_number: action.detail for action in disarms}
    assert FAILING_PR not in armed
    assert CONTROL_PR in armed
    assert CONTROL_PR not in disarmed
    assert disarmed[FAILING_PR] in {"deterministic_merge_group", "manual_dequeue"}


def test_repeated_ci_failure_removals_block_a_fresh_arm_without_run_signatures() -> None:
    node = _with_head(_open_pr_node(72, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["timelineItems"] = {
        "nodes": [
            _removal("2026-11-24T22:05:00Z", "CI_FAILURE"),
            _removal("2026-11-24T22:40:00Z", "CI_FAILURE"),
        ]
    }
    arms, disarms = _plan([node])
    assert arms == []
    assert disarms == []


def test_repeated_deterministic_failures_block_a_fresh_arm() -> None:
    node = _with_head(_open_pr_node(FAILING_PR, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["mergeGroupRuns"] = [_run(RUN_IDS[0]), _run(RUN_IDS[1])]
    arms, disarms = _plan([node])
    assert arms == []
    assert disarms == []


def test_transient_infrastructure_ejections_do_not_disarm() -> None:
    node = _with_head(_open_pr_node(70, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["autoMergeRequest"] = {"enabledAt": HEAD_AT}
    node["timelineItems"] = {
        "nodes": [
            _removal("2026-11-24T22:05:00Z", "CI_TIMEOUT"),
            _removal("2026-11-24T22:40:00Z", "CI_TIMEOUT"),
        ]
    }
    node["mergeGroupRuns"] = [
        _run(1, classification="transient", signature="runner-offline"),
        _run(2, classification="transient", signature="runner-offline"),
    ]
    _, disarms = _plan([node])
    assert disarms == []


def test_manual_dequeue_suppresses_until_retry_is_authorized() -> None:
    node = _with_head(_open_pr_node(71, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["autoMergeRequest"] = {"enabledAt": HEAD_AT}
    node["timelineItems"] = {"nodes": [_removal("2026-11-24T23:07:31Z", "MANUAL")]}
    node["comments"] = {
        "nodes": [
            {
                "createdAt": "2026-11-24T23:10:36Z",
                "author": {"login": "dieterolson"},
                "body": "owner-directed unarmed note",
            }
        ]
    }
    _, suppressed = _plan([node])
    assert [action.pr_number for action in suppressed] == [71]
    assert suppressed[0].detail == "manual_dequeue"

    node["comments"]["nodes"].append(
        {
            "createdAt": "2026-11-24T23:20:00Z",
            "author": {"login": "dieterolson"},
            "body": "retry authorized for this head",
        }
    )
    _, released = _plan([node])
    assert released == []
