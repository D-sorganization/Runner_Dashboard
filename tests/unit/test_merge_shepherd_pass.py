"""Unit tests for merge shepherd pass caps and skip rules."""

from __future__ import annotations

import pytest

from coordination.merge_shepherd_pass import (
    PassConfig,
    holds_gate_failed_payload,
    hub_hold_blocks_pr,
    load_skip_list,
    plan_repo_pass,
    pre_filter_skip_reason,
    resolve_hub_holds_policy,
    resolve_live_arming,
    run_merge_shepherd_pass,
)


def test_load_skip_list_seeds(tmp_path):
    path = tmp_path / "skip.json"
    path.write_text(
        '{"skip_prs": [{"repo": "D-sorganization/Runner_Dashboard", "number": 2658}]}',
        encoding="utf-8",
    )
    assert load_skip_list(path) == {("D-sorganization/Runner_Dashboard", 2658)}


def test_hub_holds_fail_closed_when_required_but_unreachable():
    def _boom(_url: str, _token: str) -> list:
        raise OSError("timeout")

    holds, err = resolve_hub_holds_policy(
        mode="required",
        dashboard_url="https://hub.example",
        token="tok",
        fetch_holds=_boom,
    )
    assert holds == [] and err and err.startswith("holds_unavailable:")


def test_holds_gate_failed_payload_has_no_armed_rows():
    payload = holds_gate_failed_payload(live=True, reason="holds_unavailable:not_configured")
    assert payload["armed"] == []
    assert payload["holds_unavailable"] == "holds_unavailable:not_configured"
    assert payload["status"] == "holds_unavailable"


def test_resolve_live_arming_scheduled_respects_live_var():
    assert (
        resolve_live_arming(
            merge_shepherd_live="true",
            input_dry_run="true",
            cli_dry_run=False,
            github_event_name="schedule",
        )
        is True
    )
    assert (
        resolve_live_arming(
            merge_shepherd_live="true",
            input_dry_run="true",
            cli_dry_run=False,
            github_event_name="workflow_dispatch",
        )
        is False
    )
    assert (
        resolve_live_arming(
            merge_shepherd_live="true",
            input_dry_run="false",
            cli_dry_run=False,
            github_event_name="workflow_dispatch",
        )
        is True
    )


def test_pre_filter_blocks_hold_labels():
    node = {
        "number": 2,
        "mergeStateStatus": "CLEAN",
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
        "labels": {"nodes": [{"name": "needs-dieter"}]},
    }
    assert pre_filter_skip_reason(node, repo="D-sorganization/Runner_Dashboard", skip_list=frozenset(), holds=()) == (
        "label_needs_dieter"
    )


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


def test_plan_repo_never_arms_when_hub_hold_names_pr():
    holds = [
        {
            "active": True,
            "kind": "schedule",
            "applies_to": ["repo:Runner_Dashboard"],
            "text": "C3 HOLD — block PR #500",
        }
    ]
    node = _open_pr_node(500)
    actions, skips, _ = plan_repo_pass(
        "D-sorganization/Runner_Dashboard",
        [node],
        skip_list=frozenset(),
        holds=holds,
        armed_count=0,
        queue_depth=0,
        live=True,
    )
    assert actions == []
    assert any(s.get("pr") == 500 and s.get("reason") == "hub_hold" for s in skips)


@pytest.mark.parametrize(
    "mode,url,token,fetch,expect_err",
    [
        ("required", "", "tok", None, "holds_unavailable:not_configured"),
        ("required", "https://hub", "", None, "holds_unavailable:not_configured"),
    ],
)
def test_hub_holds_unavailable_modes(mode, url, token, fetch, expect_err):
    holds, err = resolve_hub_holds_policy(mode=mode, dashboard_url=url, token=token, fetch_holds=fetch)
    assert holds == []
    assert err == expect_err


def test_hub_holds_succeed_with_fleet_token() -> None:
    fetched: list[tuple[str, str]] = []

    def _fetch(url: str, token: str) -> list[dict[str, str]]:
        fetched.append((url, token))
        return [{"active": True, "kind": "schedule", "applies_to": ["*"], "text": "ok"}]

    holds, err = resolve_hub_holds_policy(
        mode="required",
        dashboard_url="https://hub.example",
        token="fleet-bearer-secret",
        fetch_holds=_fetch,
    )
    assert err is None
    assert len(holds) == 1
    assert fetched == [("https://hub.example", "fleet-bearer-secret")]


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
