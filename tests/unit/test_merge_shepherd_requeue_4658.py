"""Offline replay: shepherd must not requeue UD #15555-style deterministic failures."""

from __future__ import annotations

from coordination.merge_shepherd import IneligibleReason, ShepherdAction, evaluate_pr_shepherd_eligibility
from coordination.merge_shepherd_pass import OPEN_PRS_QUERY, node_to_candidate, plan_repo_disarms, plan_repo_pass

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


def _check(name: str, conclusion: str = "FAILURE", *, title: str = "", summary: str = "") -> dict[str, str]:
    return {"name": name, "conclusion": conclusion, "title": title, "summary": summary}


def _removal_with_checks(
    at: str,
    reason: str,
    checks: list[dict[str, str]],
    *,
    event: str = "merge_group",
    run_id: int = 1,
) -> dict[str, object]:
    """One queue removal in the shape ``OPEN_PRS_QUERY`` returns."""
    return {
        "createdAt": at,
        "reason": reason,
        "beforeCommit": {
            "oid": "m" * 40,
            "checkSuites": {
                "nodes": [
                    {
                        "conclusion": "FAILURE",
                        "workflowRun": {"event": event, "databaseId": run_id},
                        "checkRuns": {"nodes": checks},
                    }
                ]
            },
        },
    }


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
    signature_checks = [
        _check("stale generated typescript"),
        _check("hash-seed determinism"),
    ]
    node["timelineItems"] = {
        "nodes": [
            _removal_with_checks("2026-11-24T22:05:00Z", "CI_FAILURE", signature_checks, run_id=RUN_IDS[0]),
            _removal_with_checks("2026-11-24T22:28:00Z", "CI_FAILURE", signature_checks, run_id=RUN_IDS[1]),
            _removal_with_checks("2026-11-24T22:51:00Z", "CI_FAILURE", signature_checks, run_id=RUN_IDS[2]),
            _removal_with_checks("2026-11-24T23:03:40Z", "CI_FAILURE", signature_checks, run_id=RUN_IDS[3]),
            _removal("2026-11-24T23:07:31Z", "MANUAL"),
        ]
    }
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


def test_ci_failures_without_check_evidence_do_not_disarm() -> None:
    """Two CI_FAILURE removals are not deterministic until the query returns check runs."""
    node = _with_head(_open_pr_node(72, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["autoMergeRequest"] = {"enabledAt": HEAD_AT}
    node["timelineItems"] = {
        "nodes": [
            _removal("2026-11-24T22:05:00Z", "CI_FAILURE"),
            _removal("2026-11-24T22:40:00Z", "CI_FAILURE"),
        ]
    }
    _, disarms = _plan([node])
    assert disarms == []


def test_production_query_shape_suppresses_repeated_deterministic_checks() -> None:
    node = _with_head(_open_pr_node(FAILING_PR, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    signature_checks = [
        _check("stale generated typescript"),
        _check("hash-seed determinism"),
    ]
    node["timelineItems"] = {
        "nodes": [
            _removal_with_checks("2026-11-24T22:05:00Z", "CI_FAILURE", signature_checks, run_id=RUN_IDS[0]),
            _removal_with_checks("2026-11-24T22:40:00Z", "CI_FAILURE", signature_checks, run_id=RUN_IDS[1]),
        ]
    }
    candidate = node_to_candidate(REPO, node, is_drained=False, head_sha=HEAD)
    decision = evaluate_pr_shepherd_eligibility(candidate)
    assert decision.action == ShepherdAction.SKIP
    assert decision.reason == IneligibleReason.DETERMINISTIC_REQUEUE
    assert decision.detail == "deterministic_merge_group"
    assert ("deterministic", "hash-seed determinism+stale generated typescript") in {
        (kind, signature) for _, kind, signature in candidate.merge_group_failures
    }


def test_pull_request_checks_on_the_queue_commit_are_not_merge_group_evidence() -> None:
    node = _with_head(_open_pr_node(74, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["autoMergeRequest"] = {"enabledAt": HEAD_AT}
    checks = [_check("unit tests")]
    node["timelineItems"] = {
        "nodes": [
            _removal_with_checks("2026-11-24T22:05:00Z", "CI_FAILURE", checks, event="pull_request", run_id=1),
            _removal_with_checks("2026-11-24T22:40:00Z", "CI_FAILURE", checks, event="pull_request", run_id=2),
        ]
    }
    _, disarms = _plan([node])
    assert disarms == []


def test_transient_merge_group_checks_do_not_disarm_an_armed_pr() -> None:
    node = _with_head(_open_pr_node(75, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["autoMergeRequest"] = {"enabledAt": HEAD_AT}
    checks = [_check("queue entry", "CANCELLED", summary="runner offline; lost communication with the server")]
    node["timelineItems"] = {
        "nodes": [
            _removal_with_checks("2026-11-24T22:05:00Z", "CI_FAILURE", checks, run_id=11),
            _removal_with_checks("2026-11-24T22:40:00Z", "CI_FAILURE", checks, run_id=12),
        ]
    }
    _, disarms = _plan([node])
    assert disarms == []


def test_the_open_pr_query_fetches_merge_group_check_runs() -> None:
    assert "beforeCommit" in OPEN_PRS_QUERY
    assert "checkRuns" in OPEN_PRS_QUERY
    assert "workflowRun" in OPEN_PRS_QUERY
    assert "event" in OPEN_PRS_QUERY
    assert "timelineItems(last: 100" in OPEN_PRS_QUERY
    assert "hasPreviousPage" in OPEN_PRS_QUERY
    assert "hasNextPage" in OPEN_PRS_QUERY
    assert "checkSuites(first: 10, filterBy: {appId: 15368})" in OPEN_PRS_QUERY
    assert "checkRuns(first: 20)" in OPEN_PRS_QUERY
    suites = OPEN_PRS_QUERY.split("checkSuites(first: 10, filterBy: {appId: 15368})", 1)[1].split(
        "checkRuns(first: 20)", 1
    )[0]
    runs = OPEN_PRS_QUERY.split("checkRuns(first: 20)", 1)[1].split("nodes { name conclusion title summary }", 1)[0]
    comments = OPEN_PRS_QUERY.split("comments(last: 10)", 1)[1].split("author { login }", 1)[0]
    assert "pageInfo" in suites and "hasNextPage" in suites
    assert "pageInfo" in runs and "hasNextPage" in runs
    assert "pageInfo" in comments and "hasPreviousPage" in comments


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


def test_unauthorized_comment_does_not_lift_a_manual_dequeue() -> None:
    """An unrelated commenter cannot authorize requeue through the production node."""
    node = _with_head(_open_pr_node(71, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["autoMergeRequest"] = {"enabledAt": HEAD_AT}
    node["timelineItems"] = {"nodes": [_removal("2026-11-24T23:07:31Z", "MANUAL")]}
    node["comments"] = {
        "nodes": [
            {
                "createdAt": "2026-11-24T23:20:00Z",
                "author": {"login": "unrelated-commenter"},
                "body": "retry authorized for this head",
            }
        ]
    }
    candidate = node_to_candidate(REPO, node, is_drained=False, head_sha=HEAD)
    assert candidate.comment_bodies[-1][1] == "unrelated-commenter"
    _, disarms = _plan([node])
    assert [action.detail for action in disarms] == ["manual_dequeue"]


def test_barb_direction_lifts_a_manual_dequeue() -> None:
    node = _with_head(_open_pr_node(73, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["autoMergeRequest"] = {"enabledAt": HEAD_AT}
    node["timelineItems"] = {"nodes": [_removal("2026-11-24T23:07:31Z", "MANUAL")]}
    node["comments"] = {
        "nodes": [
            {
                "createdAt": "2026-11-24T23:20:00Z",
                "author": {"login": "openbot-barb"},
                "body": "Barb direction: retry authorized for this head",
            }
        ]
    }
    _, released = _plan([node])
    assert released == []


def test_repeated_timeouts_do_not_disarm() -> None:
    node = _with_head(_open_pr_node(76, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["autoMergeRequest"] = {"enabledAt": HEAD_AT}
    checks = [_check("unit tests", "TIMED_OUT")]
    node["timelineItems"] = {
        "nodes": [
            _removal_with_checks("2026-11-24T22:05:00Z", "CI_FAILURE", checks, run_id=21),
            _removal_with_checks("2026-11-24T22:40:00Z", "CI_FAILURE", checks, run_id=22),
        ]
    }
    _, disarms = _plan([node])
    assert disarms == []


def _manual_retry_node(number: int, login: str) -> dict:
    """Armed PR manually dequeued, then a later retry note from ``login``."""
    node = _with_head(_open_pr_node(number, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["autoMergeRequest"] = {"enabledAt": HEAD_AT}
    node["timelineItems"] = {"nodes": [_removal("2026-11-24T23:07:31Z", "MANUAL")]}
    node["comments"] = {
        "nodes": [
            {
                "createdAt": "2026-11-24T23:20:00Z",
                "author": {"login": login},
                "body": "retry authorized for this head",
            }
        ]
    }
    return node


def test_deceptive_login_does_not_lift_a_manual_dequeue() -> None:
    """``not-barb`` contains the Barb token and still is not an authorized identity."""
    for number, login in ((81, "not-barb"), (82, "unrelated-barb")):
        node = _manual_retry_node(number, login)
        candidate = node_to_candidate(REPO, node, is_drained=False, head_sha=HEAD)
        assert candidate.comment_bodies[-1][1] == login
        _, disarms = _plan([node])
        assert [action.detail for action in disarms] == ["manual_dequeue"]


def test_exact_barb_bot_login_lifts_a_manual_dequeue() -> None:
    _, released = _plan([_manual_retry_node(83, "openbotgit[bot]")])
    assert released == []


def test_truncated_timeline_fails_closed_when_the_manual_dequeue_is_off_the_page() -> None:
    """Query shape: pageInfo.hasPreviousPage and only removals newer than the missing dequeue."""
    node = _with_head(_open_pr_node(84, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["autoMergeRequest"] = {"enabledAt": HEAD_AT}
    node["timelineItems"] = {
        "pageInfo": {"hasPreviousPage": True, "hasNextPage": False},
        "nodes": [_removal(f"2026-11-24T23:{minute:02d}:00Z", "CI_FAILURE") for minute in range(40, 60)],
    }
    candidate = node_to_candidate(REPO, node, is_drained=False, head_sha=HEAD)
    assert candidate.queue_history_truncated is True
    assert "MANUAL" not in {reason for _, reason in candidate.queue_removals}
    _, disarms = _plan([node])
    assert [action.detail for action in disarms] == ["incomplete_queue_history"]

    node["autoMergeRequest"] = None
    fresh = node_to_candidate(REPO, node, is_drained=False, head_sha=HEAD)
    decision = evaluate_pr_shepherd_eligibility(fresh)
    assert decision.action == ShepherdAction.SKIP
    assert decision.reason == IneligibleReason.INCOMPLETE_QUEUE_HISTORY


def test_timeline_truncated_before_the_head_does_not_hide_this_head() -> None:
    node = _with_head(_open_pr_node(85, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["autoMergeRequest"] = {"enabledAt": HEAD_AT}
    node["timelineItems"] = {
        "pageInfo": {"hasPreviousPage": True, "hasNextPage": False},
        "nodes": [_removal("2026-11-24T21:00:00Z", "CI_FAILURE")],
    }
    candidate = node_to_candidate(REPO, node, is_drained=False, head_sha=HEAD)
    assert candidate.queue_history_truncated is False
    _, disarms = _plan([node])
    assert disarms == []


def _owner_note(at: str, body: str, login: str = "dieterolson") -> dict[str, object]:
    return {"createdAt": at, "author": {"login": login}, "body": body}


def test_owner_unarmed_note_alone_blocks_arm_until_authorized_release() -> None:
    """An owner unarmed instruction is enough. No manual removal or deterministic checks required."""
    node = _with_head(_open_pr_node(91, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["comments"] = {
        "nodes": [_owner_note("2026-11-24T23:10:36Z", "owner-directed unarmed note; do not requeue this head")]
    }
    candidate = node_to_candidate(REPO, node, is_drained=False, head_sha=HEAD)
    decision = evaluate_pr_shepherd_eligibility(candidate)
    assert decision.action == ShepherdAction.SKIP
    assert decision.reason == IneligibleReason.OWNER_UNARMED
    assert decision.detail == "owner_unarmed"
    arms, _ = _plan([node])
    assert arms == []

    node["comments"]["nodes"].append(_owner_note("2026-11-24T23:40:00Z", "retry authorized for this head"))
    released = node_to_candidate(REPO, node, is_drained=False, head_sha=HEAD)
    assert evaluate_pr_shepherd_eligibility(released).reason != IneligibleReason.OWNER_UNARMED
    armed, _ = _plan([node])
    assert [action.pr_number for action in armed] == [91]


def test_new_head_does_not_release_an_owner_unarmed_hold() -> None:
    """A later commit is not an authorized release. The note predates this head and still holds."""
    new_head = "c" * 40
    node = _with_head(_open_pr_node(92, paths=["docs/readme.md"]), new_head, "2026-11-25T01:00:00Z")
    node["comments"] = {
        "nodes": [_owner_note("2026-11-24T23:10:36Z", "owner-directed unarmed note; do not requeue this head")]
    }
    candidate = node_to_candidate(REPO, node, is_drained=False, head_sha=new_head)
    decision = evaluate_pr_shepherd_eligibility(candidate)
    assert decision.action == ShepherdAction.SKIP
    assert decision.detail == "owner_unarmed"
    assert [action.pr_number for action in _plan([node])[0]] == []


def test_truncated_comment_history_fails_closed_when_the_owner_note_is_off_the_page() -> None:
    node = _with_head(_open_pr_node(93, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["comments"] = {
        "pageInfo": {"hasPreviousPage": True, "hasNextPage": False},
        "nodes": [_owner_note("2026-11-24T23:50:00Z", "shipping the docs fix", login="teammate")],
    }
    candidate = node_to_candidate(REPO, node, is_drained=False, head_sha=HEAD)
    decision = evaluate_pr_shepherd_eligibility(candidate)
    assert decision.action == ShepherdAction.SKIP
    assert decision.reason == IneligibleReason.INCOMPLETE_COMMENT_HISTORY
    assert decision.detail == "incomplete_comment_history"
    assert [action.pr_number for action in _plan([node])[0]] == []


def test_visible_retry_covers_comments_older_than_a_truncated_page() -> None:
    node = _with_head(_open_pr_node(94, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["comments"] = {
        "pageInfo": {"hasPreviousPage": True, "hasNextPage": False},
        "nodes": [_owner_note("2026-11-24T23:40:00Z", "retry authorized for this head")],
    }
    candidate = node_to_candidate(REPO, node, is_drained=False, head_sha=HEAD)
    decision = evaluate_pr_shepherd_eligibility(candidate)
    assert decision.detail != "incomplete_comment_history"
    assert decision.reason != IneligibleReason.OWNER_UNARMED
    assert [action.pr_number for action in _plan([node])[0]] == [94]


def _other_app_suite() -> dict[str, object]:
    """An empty third-party suite. Live removals return ``workflowRun: null``."""
    return {"conclusion": None, "workflowRun": None, "checkRuns": {"nodes": []}}


def test_truncated_check_pages_fail_closed_when_deterministic_runs_may_be_off_page() -> None:
    """A merge_group suite whose check runs continue on another page is incomplete."""
    node = _with_head(_open_pr_node(95, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    visible = [_check("queue entry", "FAILURE", summary="job failed without a classified cause")]
    first = _removal_with_checks("2026-11-24T22:05:00Z", "CI_FAILURE", visible, run_id=41)
    second = _removal_with_checks("2026-11-24T22:40:00Z", "CI_FAILURE", visible, run_id=42)
    for event in (first, second):
        event["beforeCommit"]["checkSuites"]["pageInfo"] = {"hasNextPage": True}
        event["beforeCommit"]["checkSuites"]["nodes"][0]["checkRuns"]["pageInfo"] = {"hasNextPage": True}
    node["timelineItems"] = {"nodes": [first, second]}
    candidate = node_to_candidate(REPO, node, is_drained=False, head_sha=HEAD)
    decision = evaluate_pr_shepherd_eligibility(candidate)
    assert decision.action == ShepherdAction.SKIP
    assert decision.reason == IneligibleReason.INCOMPLETE_CHECK_PAGE
    assert decision.detail == "incomplete_check_page"
    assert [action.pr_number for action in _plan([node])[0]] == []


def test_visible_merge_group_suite_is_not_an_incomplete_check_page() -> None:
    """Live removals lead with other apps and still have another suite page.

    Runner_Dashboard removals on 2026-11-27 returned ``hasNextPage: true`` with
    Claude and Continue occupying the first slots. A transient merge_group
    check that is already in the window must not stop requeue.
    """
    node = _with_head(_open_pr_node(96, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["autoMergeRequest"] = {"enabledAt": HEAD_AT}
    transient = [_check("queue entry", "CANCELLED", summary="runner offline; lost communication with the server")]

    def event(at: str, run_id: int) -> dict[str, object]:
        removal = _removal_with_checks(at, "CI_FAILURE", transient, run_id=run_id)
        suites = removal["beforeCommit"]["checkSuites"]
        suites["pageInfo"] = {"hasNextPage": True}
        suites["nodes"].insert(0, _other_app_suite())
        return removal

    node["timelineItems"] = {"nodes": [event("2026-11-24T22:05:00Z", 51), event("2026-11-24T22:40:00Z", 52)]}
    candidate = node_to_candidate(REPO, node, is_drained=False, head_sha=HEAD)
    decision = evaluate_pr_shepherd_eligibility(candidate)
    assert decision.detail != "incomplete_check_page"
    assert decision.reason != IneligibleReason.INCOMPLETE_CHECK_PAGE
    _, disarms = _plan([node])
    assert disarms == []


def test_truncated_suites_fail_closed_when_no_merge_group_suite_was_returned() -> None:
    """Another suite page still fails closed when every visible suite is some other app."""
    node = _with_head(_open_pr_node(97, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    removal = _removal_with_checks(
        "2026-11-24T22:05:00Z",
        "CI_FAILURE",
        [_check("unit tests")],
        event="pull_request",
        run_id=61,
    )
    removal["beforeCommit"]["checkSuites"]["pageInfo"] = {"hasNextPage": True}
    removal["beforeCommit"]["checkSuites"]["nodes"].insert(0, _other_app_suite())
    node["timelineItems"] = {"nodes": [removal]}
    candidate = node_to_candidate(REPO, node, is_drained=False, head_sha=HEAD)
    decision = evaluate_pr_shepherd_eligibility(candidate)
    assert decision.action == ShepherdAction.SKIP
    assert decision.reason == IneligibleReason.INCOMPLETE_CHECK_PAGE
    assert decision.detail == "incomplete_check_page"


def test_unclassified_failures_do_not_disarm() -> None:
    """A failure whose text never names a reproducible defect is not deterministic."""
    node = _with_head(_open_pr_node(77, paths=["docs/readme.md"]), HEAD, HEAD_AT)
    node["autoMergeRequest"] = {"enabledAt": HEAD_AT}
    checks = [_check("queue entry", "FAILURE", summary="job failed without a classified cause")]
    node["timelineItems"] = {
        "nodes": [
            _removal_with_checks("2026-11-24T22:05:00Z", "CI_FAILURE", checks, run_id=31),
            _removal_with_checks("2026-11-24T22:40:00Z", "CI_FAILURE", checks, run_id=32),
        ]
    }
    _, disarms = _plan([node])
    assert disarms == []
