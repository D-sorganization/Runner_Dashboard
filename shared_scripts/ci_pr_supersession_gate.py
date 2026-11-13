"""When a pull_request job was cancelled, only treat it as superseded (neutral) if stale."""

from __future__ import annotations


def pr_cancel_is_superseded_neutral(
    *,
    event_name: str,
    job_result: str,
    run_head_sha: str,
    pr_head_sha: str,
) -> bool:
    """True only for pull_request + cancelled when the run head is not the PR head."""
    if event_name != "pull_request" or job_result != "cancelled":
        return False
    run = (run_head_sha or "").strip().lower()
    pr = (pr_head_sha or "").strip().lower()
    if not run or not pr:
        return False
    return run != pr
