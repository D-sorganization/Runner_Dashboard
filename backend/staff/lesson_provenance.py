"""Authoritative run identity for lesson proposals; not evidence verification."""

from __future__ import annotations

from fastapi import HTTPException
from identity import Principal
from staff import store as run_stores
from staff.store import RunRecord, RunStore
from staff.tokens import active_run_id_for_principal, is_active_run_principal


def stored_lesson_run(store: RunStore, run_id: str) -> RunRecord | None:
    """Require nonempty stored provenance shared by both proposal boundaries."""
    rec = store.get_run(run_id)
    if rec is None or any(
        not isinstance(value, str) or not value.strip()
        for value in (rec.role, rec.repo, rec.requested_by, rec.standards_version)
    ):
        return None
    return rec


def authenticated_lesson_run(principal: Principal, source_run: str | None, repo: str | None) -> RunRecord:
    """Only an exact active run principal and marker can establish API provenance."""
    run_id = active_run_id_for_principal(principal.id)
    markers = {role for role in principal.roles if role.startswith("staff-run:")}
    rec = stored_lesson_run(run_stores.get_store(), run_id) if run_id else None
    if (
        rec is None
        or markers != {f"staff-run:{run_id}"}
        or not is_active_run_principal(run_id=rec.id, role=rec.role, principal_id=principal.id)
        or rec.status not in {"preparing", "running"}
        or (source_run is not None and source_run != rec.id)
        or repo != rec.repo
    ):
        raise HTTPException(status_code=403, detail="lesson proposal requires matching active stored run provenance")
    return rec


def completed_lesson_run(store: RunStore, caller: RunRecord) -> RunRecord | None:
    """Reload completion and compare caller identity before internal filing."""
    rec = stored_lesson_run(store, caller.id)
    if rec is None or rec.status != "succeeded" or rec.exit_code != 0:
        return None
    if any(
        getattr(rec, field) != getattr(caller, field)
        for field in ("role", "requested_by", "repo", "standards_version", "status", "exit_code")
    ):
        return None
    return rec
