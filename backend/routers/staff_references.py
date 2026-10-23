"""Staff API route for text-free run reference identities."""

# ruff: noqa: B008
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from identity import Principal, require_scope
from staff.run_references import StaffRunReferencesResponse, load_run_references
from staff.store import get_run_store

router = APIRouter(tags=["staff-run-references"])


@router.get("/runs/{run_id}/references", response_model=StaffRunReferencesResponse)
def run_references_v1(
    run_id: str,
    peer: Principal = Depends(require_scope("staff.read")),
) -> StaffRunReferencesResponse:
    """Return text-free reference identities for a run owned by the caller."""
    try:
        return load_run_references(get_run_store(), run_id=run_id, principal_id=peer.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid run_id") from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="run belongs to another principal") from exc
