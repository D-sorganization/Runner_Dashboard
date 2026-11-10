"""Unit tests for merge shepherd pass caps and skip rules."""

from __future__ import annotations

from coordination.merge_shepherd_pass import (
    PassConfig,
    hub_hold_blocks_pr,
    load_skip_list,
    plan_repo_pass,
    pre_filter_skip_reason,
    run_merge_shepherd_pass,
)


def test_load_skip_list_seeds(tmp_path):
    path = tmp_path / "skip.json"
    path.write_text(
        '{"skip_prs": [{"repo": "D-sorganization/Runner_Dashboard", "number": 2658}]}',
        encoding="utf-8",
    )
    assert load_skip_list(path) == {("D-sorganization/Runner_Dashboard", 2658)}


def test_pre_filter_changes_requested_and_dirty():
    node = {
        "number": 1,
        "reviewDecision": "CHANGES_REQUESTED",
        "mergeStateStatus": "CLEAN",
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
    }
    assert pre_filter_skip_reason(node, repo="D-sorganization/Runner_Dashboard", skip_list=frozenset(), holds=()) == (
        "changes_requested"
    )
    dirty = dict(node, reviewDecision=None, mergeStateStatus="DIRTY")
    assert pre_filter_skip_reason(dirty, repo="D-sorganization/Runner_Dashboard", skip_list=frozenset(), holds=()) == (
        "merge_state_dirty"
    )


def test_hub_hold_blocks_named_pr():
    holds = [
        {
            "active": True,
            "kind": "schedule",
            "applies_to": ["repo:Runner_Dashboard"],
            "text": "No arm on PR #2658 until deploy hold lifts",
        }
    ]
    assert hub_hold_blocks_pr(repo="D-sorganization/Runner_Dashboard", pr_number=2658, holds=holds) == "hub_hold"
    assert hub_hold_blocks_pr(repo="D-sorganization/Runner_Dashboard", pr_number=999, holds=holds) == ""


def _open_pr_node(number: int, *, review: str = "APPROVED", paths: list[str] | None = None) -> dict:
    file_paths = paths or ["docs/agents/x.md"]
    return {
        "number": number,
        "title": f"pr {number}",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "reviewDecision": review,
        "headRefOid": f"sha{number}",
        "additions": 12,
        "deletions": 3,
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
        "labels": {"nodes": []},
        "files": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [{"path": p} for p in file_paths],
        },
    }


def test_plan_repo_backoffs_when_queue_deep():
    nodes = [_open_pr_node(10)]
    actions, skips, backoff = plan_repo_pass(
        "D-sorganization/Runner_Dashboard",
        nodes,
        skip_list=frozenset(),
        holds=(),
        armed_count=4,
        queue_depth=3,
        live=False,
    )
    assert backoff is True
    assert actions == []


def test_plan_repo_caps_rd_arms_at_two():
    nodes = [_open_pr_node(i) for i in (100, 101, 103)]
    actions, _, backoff = plan_repo_pass(
        "D-sorganization/Runner_Dashboard",
        nodes,
        skip_list=frozenset(),
        holds=(),
        armed_count=0,
        queue_depth=0,
        live=False,
    )
    assert not backoff
    assert len(actions) == 2


def test_plan_repo_skips_without_approval_unless_docs_only():
    approved, docs_only, mixed = (
        _open_pr_node(200, review="APPROVED"),
        _open_pr_node(201, review="REVIEW_REQUIRED", paths=["changes/4245.md"]),
        _open_pr_node(202, review="REVIEW_REQUIRED", paths=["docs/a.md", "backend/x.py"]),
    )
    _, skips, _ = plan_repo_pass(
        "D-sorganization/Runner_Dashboard",
        [approved, docs_only, mixed],
        skip_list=frozenset(),
        holds=(),
        armed_count=0,
        queue_depth=0,
        live=False,
    )
    reasons = {(s["pr"], s["reason"]) for s in skips}
    assert (202, "no_approval:mixed_paths:backend/x.py") in reasons
    actions, _, _ = plan_repo_pass(
        "D-sorganization/Runner_Dashboard",
        [approved, docs_only],
        skip_list=frozenset(),
        holds=(),
        armed_count=0,
        queue_depth=0,
        live=False,
    )
    elig = {a.pr_number: a.eligibility for a in actions}
    assert elig.get(200) == "reviewed"
    assert elig.get(201) == "docs_tests_only"


def test_run_pass_is_dry_when_not_live():
    def fake_graphql(owner: str, name: str):
        return {
            "repository": {
                "mergeQueue": {"entries": {"totalCount": 0}},
                "armedPullRequests": {"totalCount": 0, "nodes": []},
                "pullRequests": {"nodes": []},
            }
        }

    summary = run_merge_shepherd_pass(config=PassConfig(live=False), fetch_graphql=fake_graphql)
    assert len(summary.repos) == 6
    assert summary.armed == []
