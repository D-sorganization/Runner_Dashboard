"""Named per-job scheduling support for roles with ``role.scope.schedules`` (500-line cap split, #3790).

Extracted from ``staff.scheduler``: a role with named jobs is scheduled per job instead of by
its flat ``role.schedule`` cron, since the flat cron has no way to express a Monday-only slot
alongside weekday ones (e.g. Bob's Monday value watch). ``queue_due_slot`` is a stateless
helper that takes the scheduler's state accessors and gate-report callback so the caller keeps
its own locking.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from staff.exclusions import ExclusionsList
from staff.roles import RoleSpec
from staff.scheduler_issue_pick import ISSUE_REMEDIATOR_ROLE, pick_issue_target
from staff.scheduler_missed_runs import note_scheduled_miss
from staff.scheduler_startup import skip_due_slot_during_startup

log = logging.getLogger("dashboard.staff.scheduler")


def named_jobs(role: RoleSpec) -> dict[str, str]:
    """Named sub-schedules declared in ``role.scope.schedules`` (e.g. Bob's Monday value watch, #3790)."""
    schedules = role.scope.get("schedules") if isinstance(role.scope, dict) else None
    return {str(k): str(v) for k, v in schedules.items()} if isinstance(schedules, dict) and schedules else {}


def queue_due_slot(
    role: RoleSpec,
    now: datetime,
    decisions: list[dict[str, Any]],
    pending_fires: list[dict[str, Any]],
    *,
    job: str | None,
    schedule_report: Callable[[RoleSpec, datetime], dict[str, Any]],
    blocker: Callable[[dict[str, Any]], str],
    load_state: Callable[[], dict[str, dict[str, Any]]],
    holds_blocking: Callable[[str, str], Any],
) -> None:
    """Evaluate one role (or one named job of it); append a skip decision or queue a fire."""
    from staff import scheduler as scheduler_mod  # deferred: scheduler imports this module

    name = role.name

    def _skip(due: str, reason: str) -> None:
        entry["last_reason"] = f"skipped {due}: {reason}"
        from staff.runner import get_runner

        note_scheduled_miss(entry, role=name, job=job, slot=due, machine=get_runner().machine)
        decision = {"role": name, "slot": due, "fired": False, "reason": reason}
        if job:
            decision["job"] = job
        decisions.append(decision)

    report = schedule_report(role, now)
    due = report["next_fire"]
    if due is None or datetime.fromisoformat(due) > now:
        return
    if skip_due_slot_during_startup(name):
        log.info("staff scheduler: %s slot %s deferred — hub startup grace", name, due)
        return
    key = f"{name}::{job}" if job else name
    entry = load_state().setdefault(key, {})
    entry["cursor"] = now.isoformat()
    blocker_reason = blocker(report)
    if blocker_reason:
        log.info("staff scheduler: %s slot %s skipped — %s", name, due, blocker_reason)
        _skip(due, blocker_reason)
        return
    repo = scheduler_mod.scheduled_repo(role.repos, now)
    issue_number: int | None = None
    if job is None and role.name == ISSUE_REMEDIATOR_ROLE:
        selected_repo, selected_issue = pick_issue_target(
            role, now, fetch_open=scheduler_mod.fetch_open_issues, fetch_claim=scheduler_mod.fetch_issue_claim_status
        )
        if not selected_repo or not selected_issue:
            _skip(due, "no eligible issues in scheduled repositories")
            return
        repo = selected_repo
        issue_number = selected_issue
    repo_hold = holds_blocking(name, repo)
    if repo_hold:
        _skip(due, f"hold: {repo_hold.text}")
        return
    excluded = ExclusionsList().blocking(repo) if repo else None
    if excluded:
        reason = f"excluded: {excluded.owner}: {excluded.reason}"
        log.info("staff scheduler: %s slot %s skipped - %s (%s)", name, due, reason, excluded.id)
        _skip(due, reason)
        return
    pending_fires.append(
        {"name": name, "role": role, "due": due, "repo": repo, "issue_number": issue_number, "job": job}
    )
