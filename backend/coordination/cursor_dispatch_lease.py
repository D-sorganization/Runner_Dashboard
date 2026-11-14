"""Pre-launch/pre-publish path-lease re-check hook for Cursor dispatch (#3931 S2).

``path_lease_store`` (S1) exists specifically for this: before a Cursor cloud
run launches, the dispatcher reserves the target (``issue:<n>`` or ``pr:<n>``)
with a TTL; right before that run's result is treated as published, it
re-checks the same scope is still held by this run, catching a concurrent
dispatch on the same target instead of silently racing it.
"""

from __future__ import annotations

from typing import Any

from path_lease_store import LeaseConflictError, PathLeaseRecord, PathLeaseStore, default_path_lease_db_path

_active_leases: dict[str, tuple[PathLeaseRecord, int | None, int | None]] = {}


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


def cursor_pre_launch_or_block(run_store: Any, plan: Any, rec: Any, wall_clock_timeout: float) -> bool:
    """Runner hook: re-check the lease right before launch; mark the run blocked on conflict.

    No-ops (returns ``True``) for non-``cursor-agent`` plans. Keeps the resulting lease in
    this module's own registry, keyed by ``rec.id``, so :func:`cursor_finalize_on_publish`
    needs no extra state threaded through the caller.
    """
    if plan.provider != "cursor-agent":
        return True
    from staff.cloud_dispatch import targets_from_record
    from staff.store import _now

    from staff import claim_guard

    issue_number, pr_number = targets_from_record(plan.target_kind, plan.target_ref)
    lease = pre_launch_lease_recheck(plan.repo, issue_number, pr_number, rec.id, wall_clock_timeout + 300.0)
    if lease is None:
        from staff.run_status import RunStatus, transition_to

        transition_to(
            run_store,
            rec.id,
            RunStatus.BLOCKED,
            failure_class="cursor_path_lease_blocked",
            ended_at=_now(),
            error="cursor dispatch path lease held by another run",
        )
        claim_guard.release_claim(rec.id, "blocked")
        run_store.append_event(rec.id, "blocked", "pre-launch path-lease re-check: scope already leased")
        return False
    _active_leases[rec.id] = (lease, issue_number, pr_number)
    return True


def cursor_finalize_on_publish(run_store: Any, rec_id: str) -> None:
    """Runner hook: re-check + release a registered Cursor lease when a cloud run publishes."""
    entry = _active_leases.pop(rec_id, None)
    if entry is None:
        return
    lease, issue_number, pr_number = entry
    if not pre_publish_lease_recheck(lease.repo, issue_number, pr_number, rec_id):
        run_store.append_event(
            rec_id, "lease", "pre-publish path-lease re-check: scope taken over by another run since launch"
        )
    release_lease_quietly(lease)
