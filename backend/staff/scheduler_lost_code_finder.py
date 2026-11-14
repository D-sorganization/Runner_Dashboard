"""Scheduler guard for Lost Code Finder pilot slots (#3869)."""

from __future__ import annotations

from datetime import datetime

from staff.barb_continuity import BarbContinuityStore
from staff.lost_code_finder_pilot import ROLE, WEEKLY_JOB

from staff import persona_fallback_lease


def fire_guard(
    role_name: str,
    *,
    continuity: BarbContinuityStore,
    job: str,
    slot: str,
    now: datetime,
) -> str:
    """Return a skip reason when the weekly Tools audit must not fire on RD."""
    if role_name != ROLE:
        return "not_lost_code_finder"
    if job != WEEKLY_JOB:
        return "unknown_job"
    if persona_fallback_lease.is_scheduler_blocked_by_fallback(continuity, role_name, now=now):
        return "fallback_lease_held"
    if persona_fallback_lease.should_skip_slot(continuity, role=role_name, job=job, slot=slot, now=now):
        return "slot_already_executed"
    return ""
