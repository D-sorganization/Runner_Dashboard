"""Unit tests for the PR-head supersession classifier in queue_cleanup.py.

Covers issue #686 requirements.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
import queue_cleanup as qc


@pytest.fixture
def mock_now() -> Generator[_dt.datetime, None, None]:
    fixed_now = _dt.datetime(2026, 5, 22, 12, 0, 0, tzinfo=_dt.UTC)
    with patch("queue_cleanup._get_now", return_value=fixed_now):
        yield fixed_now


@pytest.fixture
def mock_gh_calls() -> Generator[dict[str, Any], None, None]:
    calls: list[tuple[Any, ...]] = []
    repo_runs_queued: dict[str, Any] = {"workflow_runs": []}
    repo_runs_in_progress: dict[str, Any] = {"workflow_runs": []}
    pr_details: dict[int, Any] = {}
    existing_branches: set[str] = set()

    async def fake_gh(*args: Any, timeout: int = 30) -> tuple[int, str, str]:
        calls.append(args)
        cmd = args[0]
        if cmd == "api":
            path = args[1]
            if "actions/runs?status=queued" in path:
                return 0, json.dumps(repo_runs_queued), ""
            elif "actions/runs?status=in_progress" in path:
                return 0, json.dumps(repo_runs_in_progress), ""
            elif "/pulls/" in path:
                try:
                    pr_num = int(path.split("/pulls/")[-1])
                except ValueError:
                    return 1, "", "Invalid PR number"
                if pr_num in pr_details:
                    details = pr_details[pr_num]
                    if details is None:
                        return 1, "", "Not Found"
                    return 0, json.dumps(details), ""
                else:
                    return 1, "", "Not Found"
            elif "/branches/" in path:
                branch = path.split("/branches/")[-1]
                if branch in existing_branches:
                    return 0, json.dumps({"name": branch}), ""
                else:
                    return 1, "", "Branch Not Found"
        return 1, "", "Unknown API call"

    with patch("queue_cleanup._gh", side_effect=fake_gh):
        yield {
            "calls": calls,
            "set_queued": lambda data: repo_runs_queued.update(data),
            "set_in_progress": lambda data: repo_runs_in_progress.update(data),
            "set_prs": lambda data: pr_details.update(data),
            "set_branches": lambda data: existing_branches.update(data),
        }


def test_pr_head_matches_run_head(mock_now: _dt.datetime, mock_gh_calls: dict[str, Any]) -> None:
    """Current PR head matches run head -> not stale."""
    mock_gh_calls["set_queued"](
        {
            "workflow_runs": [
                {
                    "id": 1001,
                    "name": "CI",
                    "head_branch": "feature-1",
                    "head_sha": "sha-1",
                    "event": "pull_request",
                    "status": "queued",
                    "created_at": "2026-05-22T11:00:00Z",
                    "pull_requests": [{"number": 42}],
                }
            ]
        }
    )
    mock_gh_calls["set_prs"](
        {
            42: {
                "state": "open",
                "head": {"ref": "feature-1", "sha": "sha-1"},
            }
        }
    )
    mock_gh_calls["set_branches"]({"feature-1"})

    import asyncio

    res = asyncio.run(qc._queued_stale_for_repo("org", "repo", _dt.timedelta(minutes=60)))
    assert len(res) == 0


def test_pr_head_advanced(mock_now: _dt.datetime, mock_gh_calls: dict[str, Any]) -> None:
    """PR head advanced -> old queued run is superseded_pr_head."""
    mock_gh_calls["set_queued"](
        {
            "workflow_runs": [
                {
                    "id": 1001,
                    "name": "CI",
                    "head_branch": "feature-1",
                    "head_sha": "sha-1",
                    "event": "pull_request",
                    "status": "queued",
                    "created_at": "2026-05-22T11:00:00Z",
                    "pull_requests": [{"number": 42}],
                }
            ]
        }
    )
    mock_gh_calls["set_prs"](
        {
            42: {
                "state": "open",
                "head": {"ref": "feature-1", "sha": "sha-2"},  # advanced!
            }
        }
    )
    mock_gh_calls["set_branches"]({"feature-1"})

    import asyncio

    res = asyncio.run(qc._queued_stale_for_repo("org", "repo", _dt.timedelta(minutes=60)))
    assert len(res) == 1
    assert res[0].run_id == 1001
    assert res[0].reason == qc.StaleReason.SUPERSEDED_PR_HEAD.value
    assert res[0].safe_to_cancel is True


def test_two_queued_runs_for_same_pr(mock_now: _dt.datetime, mock_gh_calls: dict[str, Any]) -> None:
    """Two queued runs for same PR/workflow -> older run stale, newest run retained."""
    mock_gh_calls["set_queued"](
        {
            "workflow_runs": [
                {
                    "id": 1001,  # older run
                    "name": "CI",
                    "head_branch": "feature-1",
                    "head_sha": "sha-1",
                    "event": "pull_request",
                    "status": "queued",
                    "created_at": "2026-05-22T10:00:00Z",
                    "pull_requests": [{"number": 42}],
                },
                {
                    "id": 1002,  # newer run
                    "name": "CI",
                    "head_branch": "feature-1",
                    "head_sha": "sha-2",
                    "event": "pull_request",
                    "status": "queued",
                    "created_at": "2026-05-22T11:00:00Z",
                    "pull_requests": [{"number": 42}],
                },
            ]
        }
    )
    mock_gh_calls["set_prs"](
        {
            42: {
                "state": "open",
                "head": {"ref": "feature-1", "sha": "sha-2"},
            }
        }
    )
    mock_gh_calls["set_branches"]({"feature-1"})

    import asyncio

    res = asyncio.run(qc._queued_stale_for_repo("org", "repo", _dt.timedelta(minutes=60)))
    # Only the older run should be stale (Run 1001)
    assert len(res) == 1
    assert res[0].run_id == 1001
    assert res[0].reason == qc.StaleReason.SUPERSEDED_PR_HEAD.value
    assert res[0].safe_to_cancel is True


def test_closed_pr(mock_now: _dt.datetime, mock_gh_calls: dict[str, Any]) -> None:
    """Closed PR -> queued run stale."""
    mock_gh_calls["set_queued"](
        {
            "workflow_runs": [
                {
                    "id": 1001,
                    "name": "CI",
                    "head_branch": "feature-1",
                    "head_sha": "sha-1",
                    "event": "pull_request",
                    "status": "queued",
                    "created_at": "2026-05-22T11:00:00Z",
                    "pull_requests": [{"number": 42}],
                }
            ]
        }
    )
    mock_gh_calls["set_prs"](
        {
            42: {
                "state": "closed",
                "head": {"ref": "feature-1", "sha": "sha-1"},
            }
        }
    )
    mock_gh_calls["set_branches"]({"feature-1"})

    import asyncio

    res = asyncio.run(qc._queued_stale_for_repo("org", "repo", _dt.timedelta(minutes=60)))
    assert len(res) == 1
    assert res[0].run_id == 1001
    assert res[0].reason == qc.StaleReason.CLOSED_OR_DELETED_REF.value
    assert res[0].safe_to_cancel is True


def test_deleted_branch(mock_now: _dt.datetime, mock_gh_calls: dict[str, Any]) -> None:
    """Deleted branch -> queued run stale."""
    mock_gh_calls["set_queued"](
        {
            "workflow_runs": [
                {
                    "id": 1001,
                    "name": "CI",
                    "head_branch": "feature-1",
                    "head_sha": "sha-1",
                    "event": "pull_request",
                    "status": "queued",
                    "created_at": "2026-05-22T11:00:00Z",
                    "pull_requests": [{"number": 42}],
                }
            ]
        }
    )
    mock_gh_calls["set_prs"](
        {
            42: {
                "state": "open",
                "head": {"ref": "feature-1", "sha": "sha-1"},
            }
        }
    )
    # feature-1 branch is not in existing_branches

    import asyncio

    res = asyncio.run(qc._queued_stale_for_repo("org", "repo", _dt.timedelta(minutes=60)))
    assert len(res) == 1
    assert res[0].run_id == 1001
    assert res[0].reason == qc.StaleReason.CLOSED_OR_DELETED_REF.value
    assert res[0].safe_to_cancel is True


def test_push_to_main(mock_now: _dt.datetime, mock_gh_calls: dict[str, Any]) -> None:
    """Push to main -> not PR-head stale (falls back to traditional main-branch classification)."""
    mock_gh_calls["set_queued"](
        {
            "workflow_runs": [
                {
                    "id": 1001,
                    "name": "CI",
                    "head_branch": "main",
                    "head_sha": "sha-1",
                    "event": "push",
                    "status": "queued",
                    "created_at": "2026-05-22T11:00:00Z",
                    "pull_requests": [],
                }
            ]
        }
    )

    import asyncio

    res = asyncio.run(qc._queued_stale_for_repo("org", "repo", _dt.timedelta(minutes=60)))
    assert len(res) == 1
    assert res[0].run_id == 1001
    assert res[0].reason == qc.StaleReason.STALE_MAIN_BRANCH.value
    assert res[0].safe_to_cancel is False


def test_scheduled_workflow_older_than_threshold(mock_now: _dt.datetime, mock_gh_calls: dict[str, Any]) -> None:
    """Scheduled workflow older than threshold -> only stale by age policy, not PR policy."""
    mock_gh_calls["set_queued"](
        {
            "workflow_runs": [
                {
                    "id": 1001,
                    "name": "Daily Cleanup",
                    "head_branch": "main",
                    "head_sha": "sha-1",
                    "event": "schedule",
                    "status": "queued",
                    "created_at": "2026-05-22T05:00:00Z",  # 7 hours ago (> 6 hours offline-runner threshold)
                    "pull_requests": [],
                }
            ]
        }
    )

    import asyncio

    res = asyncio.run(qc._queued_stale_for_repo("org", "repo", _dt.timedelta(minutes=60)))
    assert len(res) == 1
    assert res[0].run_id == 1001
    assert res[0].reason == qc.StaleReason.OFFLINE_RUNNER_OR_LAG.value
    assert res[0].safe_to_cancel is True


def test_missing_pr_metadata(mock_now: _dt.datetime, mock_gh_calls: dict[str, Any]) -> None:
    """Missing PR metadata -> conservative unknown, not auto-cancellable."""
    mock_gh_calls["set_queued"](
        {
            "workflow_runs": [
                {
                    "id": 1001,
                    "name": "CI",
                    "head_branch": "feature-1",
                    "head_sha": "sha-1",
                    "event": "pull_request",
                    "status": "queued",
                    "created_at": "2026-05-22T11:00:00Z",
                    "pull_requests": [],  # Empty PR list for PR event
                }
            ]
        }
    )

    import asyncio

    res = asyncio.run(qc._queued_stale_for_repo("org", "repo", _dt.timedelta(minutes=60)))
    assert len(res) == 1
    assert res[0].run_id == 1001
    assert res[0].reason == qc.StaleReason.UNKNOWN.value
    assert res[0].safe_to_cancel is False


def test_in_progress_stale_behavior(mock_now: _dt.datetime, mock_gh_calls: dict[str, Any]) -> None:
    """Test in_progress runs safety checks with strict thresholds."""
    mock_gh_calls["set_in_progress"](
        {
            "workflow_runs": [
                {
                    "id": 1001,  # older in-progress, age = 150 minutes (> strict threshold of 120)
                    "name": "CI",
                    "head_branch": "feature-1",
                    "head_sha": "sha-1",
                    "event": "pull_request",
                    "status": "in_progress",
                    "created_at": "2026-05-22T09:30:00Z",
                    "pull_requests": [{"number": 42}],
                },
                {
                    "id": 1002,  # younger in-progress, age = 90 minutes (<= strict threshold of 120)
                    "name": "CI",
                    "head_branch": "feature-1",
                    "head_sha": "sha-1",
                    "event": "pull_request",
                    "status": "in_progress",
                    "created_at": "2026-05-22T10:30:00Z",
                    "pull_requests": [{"number": 42}],
                },
                {
                    "id": 1003,  # newest active run, matches head
                    "name": "CI",
                    "head_branch": "feature-1",
                    "head_sha": "sha-2",
                    "event": "pull_request",
                    "status": "in_progress",
                    "created_at": "2026-05-22T11:00:00Z",
                    "pull_requests": [{"number": 42}],
                },
            ]
        }
    )
    mock_gh_calls["set_prs"](
        {
            42: {
                "state": "open",
                "head": {"ref": "feature-1", "sha": "sha-2"},
            }
        }
    )
    mock_gh_calls["set_branches"]({"feature-1"})

    import asyncio

    res = asyncio.run(qc._queued_stale_for_repo("org", "repo", _dt.timedelta(minutes=60)))
    # Both 1001 and 1002 are stale because they are older/superseded by 1003 (which matches current head)
    # However, 1001 (150 mins) is older than strict threshold of 120 mins, so it should be safe_to_cancel = True
    # 1002 (90 mins) is not, so it should be safe_to_cancel = False
    stale_map = {r.run_id: r for r in res}
    assert 1001 in stale_map
    assert 1002 in stale_map
    assert 1003 not in stale_map

    assert stale_map[1001].reason == qc.StaleReason.SUPERSEDED_PR_HEAD.value
    assert stale_map[1001].safe_to_cancel is True

    assert stale_map[1002].reason == qc.StaleReason.SUPERSEDED_PR_HEAD.value
    assert stale_map[1002].safe_to_cancel is False
