"""GitHub Actions reaper helpers: merge-queue-safe cancellation (#2768)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from shared_scripts.merge_queue_cancel_safety import (
    MergeQueueEntry,
    may_cancel_merge_group_run,
    merge_queue_entries_from_graphql,
)

MERGE_QUEUE_BRANCH_PREFIX = "gh-readonly-queue/"


@dataclass(frozen=True)
class RunSnapshot:
    run_id: int
    event: str
    head_branch: str
    head_sha: str
    pull_request_number: int | None
    created_at: str
    status: str


def is_merge_queue_run(event: str, branch: str) -> bool:
    return event == "merge_group" or (bool(branch) and branch.startswith(MERGE_QUEUE_BRANCH_PREFIX))


def is_main_push_run(event: str, branch: str) -> bool:
    return event == "push" and (branch or "") in ("main", "master")


def should_cancel_workflow_run(
    *,
    event: str,
    head_branch: str,
    head_sha: str,
    pull_request_number: int | None,
    pr_state: str | None,
    pr_head_sha: str | None,
    merge_queue_entries: Sequence[MergeQueueEntry] | None,
) -> bool:
    if is_main_push_run(event, head_branch):
        return False
    if is_merge_queue_run(event, head_branch):
        return may_cancel_merge_group_run(
            head_ref=head_branch,
            head_sha=head_sha,
            live_entries=merge_queue_entries,
        )
    if event == "pull_request" or pull_request_number is not None:
        if pr_state in ("closed", "merged"):
            return True
        if pr_head_sha and head_sha and head_sha.lower() != pr_head_sha.lower():
            return True
        return False
    return False


def run_snapshot_from_api(run: Mapping[str, Any]) -> RunSnapshot:
    pull_requests = run.get("pull_requests") or []
    pr_number = None
    if pull_requests and isinstance(pull_requests[0], Mapping):
        pr_number = pull_requests[0].get("number")
    return RunSnapshot(
        run_id=int(run.get("id") or 0),
        event=str(run.get("event") or ""),
        head_branch=str(run.get("head_branch") or ""),
        head_sha=str(run.get("head_sha") or ""),
        pull_request_number=int(pr_number) if pr_number is not None else None,
        created_at=str(run.get("created_at") or ""),
        status=str(run.get("status") or ""),
    )


def select_cancellable_runs(
    runs: Sequence[RunSnapshot],
    *,
    merge_queue_entries: Sequence[MergeQueueEntry] | None,
    pr_metadata: Mapping[int, Mapping[str, str]],
) -> list[RunSnapshot]:
    selected: list[RunSnapshot] = []
    for run in runs:
        meta = pr_metadata.get(run.pull_request_number or -1, {})
        if should_cancel_workflow_run(
            event=run.event,
            head_branch=run.head_branch,
            head_sha=run.head_sha,
            pull_request_number=run.pull_request_number,
            pr_state=meta.get("state"),
            pr_head_sha=meta.get("head_sha"),
            merge_queue_entries=merge_queue_entries,
        ):
            selected.append(run)
    return selected

