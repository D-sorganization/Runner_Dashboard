"""Unit tests for merge shepherd pass caps and skip rules."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from coordination.merge_shepherd_exact_head import HeadReviewPass
from coordination.merge_shepherd_exact_head import collect_review_passes_from_store
from coordination.merge_shepherd_pass import (
    PassConfig,
    count_armed_open_pull_requests,
    graphql_nodes,
    holds_gate_failed_payload,
    hub_hold_blocks_pr,
    is_repo_access_error,
    load_skip_list,
    plan_repo_pass,
    pre_filter_skip_reason,
    resolve_hub_holds_policy,
    resolve_live_arming,
    resolve_merge_shepherd_review_store,
    run_merge_shepherd_pass,
    stitch_armed_pull_request_pages,
    summary_to_json,
)
from staff.store import RunRecord, RunStore


def _review_pass(pr: int, head: str) -> HeadReviewPass:
    return HeadReviewPass(
        pr=pr,
        head_sha=head,
        verdict="approve",
        # #4414: a fixed date aged past the 72 h review-pass window on 2026-11-13 and the
        # planner stopped arming; a pass is "recorded now" whenever the test runs.
        recorded_at=datetime.now(UTC).isoformat(),
    )


def test_resolve_merge_shepherd_review_store_uses_runstore_path_not_repo_checkout(
    tmp_path, monkeypatch
) -> None:
    repo_root = tmp_path / "checkout"
    repo_root.mkdir()
    db_path = tmp_path / "config" / "staff_runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db_path))

    store = RunStore(path=db_path)
    store.create_run(
        RunRecord(
            id="review-run-1",
            role="code-reviewer",
            provider="openai",
            model="gpt-test",
            machine="hub",
            repo="Runner_Dashboard",
            target_kind="pr",
            target_ref="42",
            prompt="review",
            status="succeeded",
            outcome="STAFF_RESULT: review approve #42 @0000ab010000",
            ended_at="2026-11-10T12:00:00+00:00",
        )
    )

    repo_only = (
        repo_root / "data" / "staff_runs.sqlite3",
        repo_root / "staff_runs.sqlite3",
    )
    assert not any(path.is_file() for path in repo_only)

    resolved = resolve_merge_shepherd_review_store(repo_root=repo_root)
    assert resolved is not None
    passes = collect_review_passes_from_store(resolved, "Runner_Dashboard")
    assert len(passes) == 1
    assert passes[0].pr == 42
    assert passes[0].head_sha == "0000ab010000"


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
        review_passes=[_review_pass(10, "sha10")],
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
        review_passes=[
            _review_pass(100, "sha100"),
            _review_pass(101, "sha101"),
            _review_pass(103, "sha103"),
        ],
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
        review_passes=[_review_pass(200, "sha200")],
    )
    elig = {a.pr_number: a.eligibility for a in actions}
    assert elig.get(200) == "reviewed"
    assert elig.get(201) == "docs_tests_only"


def test_plan_repo_skips_approved_without_exact_head_pass() -> None:
    node = _open_pr_node(300, review="APPROVED", paths=["backend/server.py"])
    _, skips, _ = plan_repo_pass(
        "D-sorganization/Runner_Dashboard",
        [node],
        skip_list=frozenset(),
        holds=(),
        armed_count=0,
        queue_depth=0,
        live=False,
        review_passes=[],
    )
    assert (300, "no_approval:missing_pass_at_head") in {(s["pr"], s["reason"]) for s in skips}


def test_plan_repo_honours_merge_hold_callback() -> None:
    node = _open_pr_node(400, review="REVIEW_REQUIRED", paths=["docs/readme.md"])
    holds = lambda _repo, pr: "reviewer disabled auto-merge" if pr == 400 else ""
    _, skips, _ = plan_repo_pass(
        "D-sorganization/Runner_Dashboard",
        [node],
        skip_list=frozenset(),
        holds=(),
        armed_count=0,
        queue_depth=0,
        live=False,
        hold_reason=holds,
    )
    assert any(s.get("pr") == 400 and s.get("reason", "").startswith("merge_hold:") for s in skips)


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


def test_count_armed_open_pull_requests_ignores_unarmed_nodes() -> None:
    nodes = [
        {"number": 1, "autoMergeRequest": {"enabledAt": "2026-11-10T00:00:00Z"}},
        {"number": 2, "autoMergeRequest": None},
        {"number": 3},
    ]
    assert count_armed_open_pull_requests(nodes) == 1


def test_graphql_armed_count_uses_auto_merge_not_open_total() -> None:
    armed_nodes = [{"number": i, "autoMergeRequest": {"enabledAt": "t"} if i <= 2 else None} for i in range(1, 11)]
    payload = {
        "repository": {
            "mergeQueue": {"entries": {"totalCount": 0}},
            "armedPullRequests": {"totalCount": 10, "nodes": armed_nodes},
            "pullRequests": {"nodes": []},
        }
    }
    _, armed_count, _ = graphql_nodes(payload)
    assert armed_count == 2


def test_stitch_armed_pull_request_pages_includes_armed_beyond_first_page() -> None:
    page1 = {
        "nodes": [{"number": i, "autoMergeRequest": None} for i in range(1, 101)],
        "pageInfo": {"hasNextPage": True, "endCursor": "page-1"},
    }
    page2 = {
        "nodes": [{"number": i, "autoMergeRequest": {"enabledAt": "t"}} for i in range(101, 108)],
        "pageInfo": {"hasNextPage": False, "endCursor": "page-2"},
    }
    stitched = stitch_armed_pull_request_pages([page1, page2])
    assert count_armed_open_pull_requests(stitched["nodes"]) == 7

    first_page_only = {
        "repository": {
            "mergeQueue": {"entries": {"totalCount": 0}},
            "armedPullRequests": page1,
            "pullRequests": {"nodes": []},
        }
    }
    _, undercount, _ = graphql_nodes(first_page_only)
    assert undercount == 0

    stitched_payload = {
        "repository": {
            "mergeQueue": {"entries": {"totalCount": 0}},
            "armedPullRequests": stitched,
            "pullRequests": {"nodes": []},
        }
    }
    _, armed_count, _ = graphql_nodes(stitched_payload)
    assert armed_count == 7


def test_plan_repo_backoffs_when_armed_only_on_second_graphql_page() -> None:
    page1 = {
        "nodes": [{"number": i, "autoMergeRequest": None} for i in range(1, 101)],
        "pageInfo": {"hasNextPage": True, "endCursor": "page-1"},
    }
    page2 = {
        "nodes": [{"number": i, "autoMergeRequest": {"enabledAt": "t"}} for i in range(101, 108)],
        "pageInfo": {"hasNextPage": False, "endCursor": "page-2"},
    }
    armed_count = count_armed_open_pull_requests(stitch_armed_pull_request_pages([page1, page2])["nodes"])
    nodes = [_open_pr_node(1)]
    _, _, backoff = plan_repo_pass(
        "D-sorganization/Runner_Dashboard",
        nodes,
        skip_list=frozenset(),
        holds=(),
        armed_count=armed_count,
        queue_depth=0,
        live=False,
    )
    assert backoff is True


def test_plan_repo_no_backoff_when_many_open_but_only_two_armed() -> None:
    nodes = [_open_pr_node(i, review="APPROVED") for i in range(1, 11)]
    _, _, backoff = plan_repo_pass(
        "D-sorganization/Runner_Dashboard",
        nodes,
        skip_list=frozenset(),
        holds=(),
        armed_count=2,
        queue_depth=0,
        live=False,
    )
    assert backoff is False


def test_plan_repo_backoffs_when_seven_armed() -> None:
    nodes = [_open_pr_node(1)]
    _, _, backoff = plan_repo_pass(
        "D-sorganization/Runner_Dashboard",
        nodes,
        skip_list=frozenset(),
        holds=(),
        armed_count=7,
        queue_depth=0,
        live=False,
    )
    assert backoff is True


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


# Captured from scheduled run 43363423325 (2026-11-13): gh exits non-zero with one
# denial line per open PR node when the App token cannot read a non-public repo.
_REAL_ACCESS_DENIED = "gh: Resource not accessible by integration" + ("\nResource not accessible by integration" * 11)
_EMPTY_REPO_PAYLOAD = {
    "repository": {
        "mergeQueue": {"entries": {"totalCount": 0}},
        "armedPullRequests": {"totalCount": 0, "nodes": []},
        "pullRequests": {"nodes": []},
    }
}


def _fetch_failing_for(failures: dict[str, str]):
    def fetch(owner: str, name: str):
        message = failures.get(name)
        if message is not None:
            raise OSError(message)
        return _EMPTY_REPO_PAYLOAD

    return fetch


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (_REAL_ACCESS_DENIED, True),
        ("gh: Resource not accessible by integration", True),
        ("gh: Could not resolve to a Repository with the name 'D-sorganization/Tools_Private'.", True),
        ("", False),
        ("graphql failed", False),
        ("gh: HTTP 401: Bad credentials (https://api.github.com/graphql)", False),
        ("gh: API rate limit exceeded for installation ID 1.", False),
        ("gh: Resource not accessible by integration\nSomething went wrong while executing your query.", False),
        ("armed_pull_requests_pagination_incomplete:missing_end_cursor", False),
    ],
)
def test_is_repo_access_error_is_narrow(message: str, expected: bool) -> None:
    assert is_repo_access_error(message) is expected


def test_run_pass_skips_inaccessible_repo_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    fetch = _fetch_failing_for({"Repository_Management": _REAL_ACCESS_DENIED})
    with caplog.at_level("WARNING"):
        summary = run_merge_shepherd_pass(config=PassConfig(live=False), fetch_graphql=fetch)

    assert summary.errors == []
    assert summary.inaccessible_repos == [
        {
            "repo": "D-sorganization/Repository_Management",
            "reason": "Resource not accessible by integration",
        }
    ]
    assert len(summary.repos) == 5
    assert "D-sorganization/Repository_Management" not in [row["repo"] for row in summary.repos]
    assert "D-sorganization/Repository_Management" in caplog.text
    assert summary_to_json(summary)["inaccessible_repos"] == summary.inaccessible_repos


def test_run_pass_keeps_real_fetch_errors_fatal() -> None:
    fetch = _fetch_failing_for({"UpstreamDrift": "gh: HTTP 502: Bad Gateway"})
    summary = run_merge_shepherd_pass(config=PassConfig(live=False), fetch_graphql=fetch)

    assert summary.errors == ["D-sorganization/UpstreamDrift: gh: HTTP 502: Bad Gateway"]
    assert summary.inaccessible_repos == []


def test_run_pass_fails_when_every_repo_is_inaccessible() -> None:
    def fetch(owner: str, name: str):
        raise OSError(_REAL_ACCESS_DENIED)

    summary = run_merge_shepherd_pass(config=PassConfig(live=False), fetch_graphql=fetch)

    assert summary.repos == []
    assert len(summary.inaccessible_repos) == 6
    assert summary.errors == ["all_repos_inaccessible:6"]
