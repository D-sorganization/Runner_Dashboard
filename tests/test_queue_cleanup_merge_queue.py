"""Merge-queue (merge_group) runs must never be cancellable (issue #1915).

Cancelling a merge-group run cancels the required check, so GitHub dequeues
the PR with CI_FAILURE and disarms auto-merge. Every queue canceller
(`find_stale_runs` backing `/api/queue/stale`, `/api/queue/purge-stale`, the
`reap_queued_jobs.py` client and staff `maintenance.queue_purge_stale`) goes
through `queue_cleanup._queued_stale_for_repo`, so exempting there covers all.
"""

from __future__ import annotations

import asyncio
import datetime as _dt

import pytest
import queue_cleanup as qc

QUEUE_BRANCH = "gh-readonly-queue/main/pr-1-abc"


def _iso_ago(minutes: int) -> str:
    return (qc._get_now() - _dt.timedelta(minutes=minutes)).isoformat()


@pytest.mark.parametrize(
    ("event", "branch", "expected"),
    [
        ("merge_group", "feature/x", True),
        ("push", QUEUE_BRANCH, True),
        ("merge_group", QUEUE_BRANCH, True),
        ("pull_request", "feature/x", False),
        ("push", "feature/gh-readonly-queue", False),
        ("", "", False),
    ],
)
def test_is_merge_queue_run(event: str, branch: str, expected: bool) -> None:
    assert qc.is_merge_queue_run(event, branch) is expected


def test_is_protected_target_covers_merge_queue() -> None:
    assert qc.is_protected_target(QUEUE_BRANCH, "merge_group", "CI") is True


def _scan(monkeypatch, runs, online):
    async def fake(*args, **_kwargs):
        path = args[1] if len(args) > 1 else ""
        if "status=queued" in path:
            return {"workflow_runs": runs}
        if "/jobs" in path:
            return {"jobs": [{"status": "queued", "labels": ["dead-label"]}]}
        return {"workflow_runs": []}

    monkeypatch.setattr(qc, "_gh_json", fake)
    return asyncio.run(qc._queued_stale_for_repo("org", "repo", _dt.timedelta(minutes=60), online))


@pytest.mark.parametrize(
    ("event", "branch"),
    [("merge_group", "feature/x"), ("merge_group", QUEUE_BRANCH), ("push", QUEUE_BRANCH)],
)
@pytest.mark.parametrize("online", [None, [frozenset({"d-sorg-fleet"})]])
def test_stale_queued_merge_queue_run_is_kept(monkeypatch, event, branch, online) -> None:
    runs = [
        {"id": 7, "name": "CI", "head_branch": branch, "status": "queued", "created_at": _iso_ago(600), "event": event}
    ]
    assert _scan(monkeypatch, runs, online or []) == []


def test_ordinary_stale_feature_run_still_flagged(monkeypatch) -> None:
    runs = [
        {
            "id": 8,
            "name": "CI",
            "head_branch": "feature/x",
            "status": "queued",
            "created_at": _iso_ago(600),
            "event": "push",
        }
    ]
    out = _scan(monkeypatch, runs, [])
    assert [r.run_id for r in out] == [8]
