"""GitHub workflow reaper: merge-queue-safe cancellation (#2768)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from shared_scripts.github_workflow_reaper import (
    RunSnapshot,
    is_main_push_run,
    select_cancellable_runs,
    should_cancel_workflow_run,
)
from shared_scripts.merge_queue_cancel_safety import MergeQueueEntry

LIVE_REF = "gh-readonly-queue/main/pr-77-abc1234567890abcdef0123456789abcdef01"
LIVE_SHA = "abc1234567890abcdef0123456789abcdef01"
OLD_REF = "gh-readonly-queue/main/pr-77-deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
OLD_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
LIVE = [MergeQueueEntry(head_ref=LIVE_REF, head_sha=LIVE_SHA)]


@pytest.mark.parametrize(
    ("event", "branch", "head_sha", "pr_state", "pr_head", "entries", "expect"),
    [
        ("push", "main", "a", None, None, [], False),
        ("merge_group", LIVE_REF, LIVE_SHA, None, None, LIVE, False),
        ("merge_group", OLD_REF, OLD_SHA, None, None, LIVE, True),
        ("merge_group", OLD_REF, OLD_SHA, None, None, None, False),
        ("pull_request", "feat", "oldsha", "open", "newsha", [], True),
        ("pull_request", "feat", "same", "open", "same", [], False),
        ("pull_request", "feat", "same", "closed", "same", [], True),
    ],
)
def test_should_cancel_workflow_run_matrix(
    event: str,
    branch: str,
    head_sha: str,
    pr_state: str | None,
    pr_head: str | None,
    entries: list[MergeQueueEntry] | None,
    expect: bool,
) -> None:
    assert is_main_push_run("push", "main") is True
    assert (
        should_cancel_workflow_run(
            event=event,
            head_branch=branch,
            head_sha=head_sha,
            pull_request_number=12 if event == "pull_request" else None,
            pr_state=pr_state,
            pr_head_sha=pr_head,
            merge_queue_entries=entries,
        )
        is expect
    )


def test_select_cancellable_runs_skips_main_push() -> None:
    runs = [
        RunSnapshot(1, "push", "main", "a", None, "2026-01-01T00:00:00Z", "queued"),
        RunSnapshot(2, "pull_request", "feat", "old", 3, "2026-01-01T00:00:00Z", "queued"),
    ]
    selected = select_cancellable_runs(runs, merge_queue_entries=[], pr_metadata={3: {"state": "open", "head_sha": "new"}})
    assert [r.run_id for r in selected] == [2]


def test_script_fails_without_token() -> None:
    import subprocess

    root = Path(__file__).resolve().parents[1]
    env = {"PYTHONPATH": str(root), "GITHUB_REPOSITORY": "D-sorganization/Runner_Dashboard"}
    proc = subprocess.run(
        [sys.executable, "scripts/reap_github_workflow_runs.py", "--dry-run", "true"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0


def test_script_fails_when_merge_queue_unreadable() -> None:
    root = Path(__file__).resolve().parents[1]
    scripts = root / "scripts"
    sys.path.insert(0, str(scripts))
    import reap_github_workflow_runs  # noqa: E402

    argv = ["reap_github_workflow_runs.py", "--dry-run", "true"]
    with patch.dict(
        "os.environ",
        {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "D-sorganization/Runner_Dashboard"},
        clear=False,
    ):
        with patch.object(sys, "argv", argv):
            with patch.object(
                reap_github_workflow_runs,
                "fetch_merge_queue_entries",
                side_effect=RuntimeError("mergeQueue GraphQL errors"),
            ):
                with pytest.raises(SystemExit) as exc:
                    reap_github_workflow_runs.main()
    assert exc.value.code == 1
