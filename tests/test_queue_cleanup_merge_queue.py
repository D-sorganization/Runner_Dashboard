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


# --- classify_stale_run / reaper / staff cancellers (#1915) ---------------------


@pytest.mark.parametrize(
    ("branch", "event"), [(QUEUE_BRANCH, ""), ("feature/x", "merge_group"), (QUEUE_BRANCH, "merge_group")]
)
def test_classify_stale_run_keeps_merge_queue_runs(branch: str, event: str) -> None:
    reason, safe = qc.classify_stale_run(branch, age_minutes=600, event=event)
    assert reason == qc.StaleReason.PROTECTED_MERGE_QUEUE.value
    assert safe is False


def test_reaper_selection_drops_merge_queue_runs() -> None:
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "reap_queued_jobs", Path(__file__).resolve().parents[1] / "scripts" / "reap_queued_jobs.py"
    )
    assert spec and spec.loader
    reaper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reaper)

    runs = [
        {"run_id": 1, "branch": QUEUE_BRANCH, "event": "merge_group", "safe_to_cancel": True},
        {"run_id": 2, "branch": "feature/x", "event": "push", "safe_to_cancel": True},
    ]
    assert [r["run_id"] for r in reaper.select_cancellable(runs)] == [2]


def test_staff_queue_purge_stale_cannot_cancel_merge_queue_runs() -> None:
    from staff import maintenance

    # The staff action is not wired to a canceller; if it is ever wired it must go
    # through queue_cleanup.find_stale_runs, which never returns merge-queue runs.
    with pytest.raises(maintenance.MaintenanceNotWiredError):
        maintenance._purge_stale_queue(None, 60, 10)


def test_reaper_aborts_before_purge_when_candidates_include_merge_queue(monkeypatch) -> None:
    import importlib.util
    import io
    import json
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "reap_queued_jobs_abort", Path(__file__).resolve().parents[1] / "scripts" / "reap_queued_jobs.py"
    )
    assert spec and spec.loader
    reaper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reaper)

    calls: list[str] = []

    def fake_urlopen(req, timeout=0):
        calls.append(req.full_url)
        body = {"stale_count": 1, "runs": [{"run_id": 1, "branch": QUEUE_BRANCH}]}
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(reaper.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sys, "argv", ["reap", "--dry-run", "false"])
    monkeypatch.delenv("QUEUED_JOB_REAPER_DISABLED", raising=False)
    with pytest.raises(SystemExit) as exc:
        reaper.main()
    assert exc.value.code == 1
    assert all("purge-stale" not in url for url in calls)
