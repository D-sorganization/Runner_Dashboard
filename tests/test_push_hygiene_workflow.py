"""Workflow and GitHub API contract tests for push-hygiene (issue #2892)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from shared_scripts import push_hygiene

_WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
_CI_STANDARD = _WORKFLOWS / "ci-standard.yml"
_ACTION = Path(__file__).resolve().parents[1] / ".github" / "actions" / "push-hygiene" / "action.yml"


def test_push_hygiene_action_exists_and_calls_shared_script() -> None:
    assert _ACTION.is_file(), "missing .github/actions/push-hygiene/action.yml"
    data = yaml.safe_load(_ACTION.read_text(encoding="utf-8"))
    run_block = str(data["runs"]["steps"])
    assert "shared_scripts/push_hygiene.py" in run_block
    assert "--github-workflow" in run_block


def test_ci_standard_push_hygiene_job_runs_on_synchronize_only() -> None:
    data = yaml.safe_load(_CI_STANDARD.read_text(encoding="utf-8"))
    job = data["jobs"]["push-hygiene"]
    job_if = str(job.get("if") or "")
    assert "synchronize" in job_if
    assert "pull_request" in job_if
    assert "head.repo.full_name == github.repository" in job_if


def test_ci_heavy_jobs_tolerate_skipped_push_hygiene() -> None:
    data = yaml.safe_load(_CI_STANDARD.read_text(encoding="utf-8"))
    jobs = data["jobs"]
    for job_name in ("ci-health-check", "lint", "tests", "quality-gate"):
        job = jobs[job_name]
        assert "push-hygiene" in str(job.get("needs")), job_name
        job_if = str(job.get("if") or "")
        assert "needs.push-hygiene.result == 'skipped'" in job_if, job_name


@pytest.mark.parametrize(
    ("payload", "expected_auto", "expected_queue"),
    [
        (
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "number": 42,
                            "state": "OPEN",
                            "autoMergeRequest": {"enabledAt": "2026-01-01T00:00:00Z"},
                            "mergeQueueEntry": None,
                        }
                    }
                }
            },
            True,
            False,
        ),
        (
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "number": 43,
                            "state": "OPEN",
                            "autoMergeRequest": None,
                            "mergeQueueEntry": {"state": "QUEUED"},
                        }
                    }
                }
            },
            False,
            True,
        ),
        (
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "number": 44,
                            "state": "CLOSED",
                            "autoMergeRequest": {"enabledAt": "2026-01-01T00:00:00Z"},
                            "mergeQueueEntry": None,
                        }
                    }
                }
            },
            True,
            False,
        ),
    ],
)
def test_pull_request_queue_state_from_graphql(payload: dict, expected_auto: bool, expected_queue: bool) -> None:
    state = push_hygiene.pull_request_queue_state_from_graphql(payload)
    assert state.auto_merge_enabled is expected_auto
    assert state.in_merge_queue is expected_queue
    assert state.pr_number == payload["data"]["repository"]["pullRequest"]["number"]


def test_run_workflow_guard_posts_comment_once_on_failure() -> None:
    commit = push_hygiene.CommitInfo(
        "aaa111111111",
        "tree00000000",
        ("bbb222222222",),
        ("tree00000000",),
        False,
        "empty",
    )
    armed = push_hygiene.PullRequestQueueState(pr_number=55, auto_merge_enabled=True)
    decision = push_hygiene.evaluate_push_hygiene(commit, armed)
    assert not decision.allowed

    posted: list[str] = []

    def fake_post(_repo: str, _pr: int, _token: str, body: str) -> None:
        posted.append(body)

    with patch.object(push_hygiene, "fetch_pull_request_queue_state", return_value=armed):
        rc = push_hygiene.run_workflow_guard(
            repo="o/r",
            pr_number=55,
            token="tok",
            commit=commit,
            fetch_comment_bodies=lambda: [],
            post_comment=fake_post,
        )
    assert rc == 1
    assert len(posted) == 1
    assert push_hygiene.COMMENT_MARKER in posted[0]

    with patch.object(push_hygiene, "fetch_pull_request_queue_state", return_value=armed):
        rc2 = push_hygiene.run_workflow_guard(
            repo="o/r",
            pr_number=55,
            token="tok",
            commit=commit,
            fetch_comment_bodies=lambda: posted,
            post_comment=fake_post,
        )
    assert rc2 == 1
    assert len(posted) == 1


def test_run_workflow_guard_allows_clean_commit_when_unarmed() -> None:
    commit = push_hygiene.CommitInfo(
        "aaa111111111",
        "tree00000000",
        ("bbb222222222",),
        ("tree00000000",),
        False,
        "empty",
    )
    unarmed = push_hygiene.PullRequestQueueState(pr_number=1, auto_merge_enabled=False, in_merge_queue=False)

    with patch.object(push_hygiene, "fetch_pull_request_queue_state", return_value=unarmed):
        rc = push_hygiene.run_workflow_guard(
            repo="o/r",
            pr_number=1,
            token="tok",
            commit=commit,
            fetch_comment_bodies=lambda: [],
            post_comment=lambda *_a, **_k: None,
        )
    assert rc == 0
