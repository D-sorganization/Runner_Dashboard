"""Staff Hub dispatch-exclusions API.

Routes (mounted under ``/api/staff`` and ``/api/v1/staff``):
  GET    /exclusions        Active (and expired, flagged) dispatch exclusions.
  POST   /exclusions        Add an exclusion (reason + owner required).
  DELETE /exclusions/{id}   Lift an exclusion.

Auth mirrors the holds routes: reads need ``staff.read``, writes
``staff.holds.write`` (orchestrator peer). See ``staff.exclusions``.
"""

# ruff: noqa: B008
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from identity import Principal, format_caller, require_scope
from pydantic import BaseModel, Field
from staff.audit import record_audit
from staff.exclusions import MAX_TEXT, ExclusionsList

log = logging.getLogger("dashboard.staff.exclusions")
router = APIRouter(tags=["staff"])


class ExclusionBody(BaseModel):
    """POST body. DbC: the JSON shape of ``staff.exclusions.Exclusion`` minus server-set fields."""

    repo: str = Field(min_length=1, max_length=120)
    issue: int | None = Field(default=None, ge=1)
    path_glob: str = Field(default="", max_length=200)
    owner: str = Field(min_length=1, max_length=MAX_TEXT)
    reason: str = Field(min_length=1, max_length=MAX_TEXT)
    expires_at: str | None = Field(default=None, max_length=40)


def _view(exclusions: ExclusionsList) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    return [{**e.to_dict(), "active": e.is_active(now)} for e in exclusions.load()]


@router.get("/exclusions")
async def list_exclusions(_peer: Principal = Depends(require_scope("staff.read"))) -> dict[str, Any]:
    store = ExclusionsList()
    return {"exclusions": _view(store), "path": str(store.path)}


@router.post("/exclusions", status_code=201)
async def add_exclusion(
    body: ExclusionBody, caller: Principal = Depends(require_scope("staff.holds.write"))
) -> dict[str, Any]:
    """Add (or replace the identical repo/issue/glob) exclusion; the caller is recorded as ``created_by``."""
    caller_str = format_caller(caller)
    try:
        entry = ExclusionsList().add(body.model_dump(), created_by=caller_str)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_audit(
        action="hold_set",
        target=f"exclusion:{entry.id}",
        principal=caller_str,
        surface="api",
        outcome="success",
        detail={"repo": entry.repo, "issue": entry.issue, "owner": entry.owner},
        fail_closed=True,
    )
    log.info("staff: dispatch exclusion %s added by %s (%s#%s)", entry.id, caller_str, entry.repo, entry.issue)
    return {"exclusion": entry.to_dict()}


@router.delete("/exclusions/{exclusion_id}")
async def delete_exclusion(
    exclusion_id: str, caller: Principal = Depends(require_scope("staff.holds.write"))
) -> dict[str, Any]:
    if not ExclusionsList().remove(exclusion_id):
        raise HTTPException(status_code=404, detail=f"exclusion {exclusion_id} not found")
    caller_str = format_caller(caller)
    record_audit(
        action="hold_clear",
        target=f"exclusion:{exclusion_id}",
        principal=caller_str,
        surface="api",
        outcome="success",
        fail_closed=True,
    )
    log.info("staff: dispatch exclusion %s lifted by %s", exclusion_id, caller_str)
    return {"removed": exclusion_id}
