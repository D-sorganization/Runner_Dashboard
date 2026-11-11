"""Unit tests for merge shepherd pass caps and skip rules."""

from __future__ import annotations

import pytest

from coordination.merge_shepherd_exact_head import HeadReviewPass
from coordination.merge_shepherd_exact_head import collect_review_passes_from_store
from coordination.merge_shepherd_pass import (
    PassConfig,
    holds_gate_failed_payload,
    hub_hold_blocks_pr,
    load_skip_list,
    plan_repo_pass,
    pre_filter_skip_reason,
    resolve_hub_holds_policy,
    resolve_live_arming,
    resolve_merge_shepherd_review_store,
    run_merge_shepherd_pass,
)
from staff.store import RunRecord, RunStore


def _review_pass(pr: int, head: str) -> HeadReviewPass:
    return HeadReviewPass(
        pr=pr,
        head_sha=head,
        verdict="approve",
        recorded_at="2026-11-10T12:00:00+00:00",
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
