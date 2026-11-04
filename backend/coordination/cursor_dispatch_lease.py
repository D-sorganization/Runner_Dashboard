"""Pre-launch/pre-publish path-lease re-check hook for Cursor dispatch (#3931 S2).

``path_lease_store`` (S1) exists specifically for this: before a Cursor cloud
run launches, the dispatcher reserves the target (``issue:<n>`` or ``pr:<n>``)
with a TTL; right before that run's result is treated as published, it
re-checks the same scope is still held by this run, catching a concurrent
dispatch on the same target instead of silently racing it.
"""

from __future__ import annotations

from path_lease_store import LeaseConflictError, PathLeaseRecord, PathLeaseStore, default_path_lease_db_path


def cursor_dispatch_scope(issue_number: int | None, pr_number: int | None) -> str:
    """The lease scope key for a Cursor dispatch target: ``issue:<n>`` or ``pr:<n>``."""
    if issue_number:
        return f"issue:{issue_number}"
    if pr_number:
        return f"pr:{pr_number}"
    raise ValueError("Cursor dispatch requires an issue or PR number for its lease scope")


def pre_launch_lease_recheck(
    repo: str, issue_number: int | None, pr_number: int | None, owner: str, ttl_seconds: float
) -> PathLeaseRecord | None:
    """Acquire the path lease right before launching Cursor.

    Returns the lease record on success, or ``None`` when another owner
    already holds this scope (the launch should be blocked).
    """
    store = PathLeaseStore(default_path_lease_db_path())
    scope = cursor_dispatch_scope(issue_number, pr_number)
    try:
        return store.acquire(repo, scope, owner, ttl_seconds)
    except LeaseConflictError:
        return None


def pre_publish_lease_recheck(repo: str, issue_number: int | None, pr_number: int | None, owner: str) -> bool:
    """``True`` when *owner* still holds (or nobody holds) the scope, so it is safe to publish."""
    store = PathLeaseStore(default_path_lease_db_path())
    scope = cursor_dispatch_scope(issue_number, pr_number)
    rec = store.check(repo, scope)
    return rec is None or rec.owner == owner


def release_lease_quietly(lease: PathLeaseRecord) -> None:
    """Best-effort release once Cursor dispatch is done with this scope; never raises."""
    store = PathLeaseStore(default_path_lease_db_path())
    try:
        store.release(lease.lease_id, lease.owner)
    except LeaseConflictError:
        pass
