"""Fleet agent routine endpoints (#3640+)."""

# ruff: noqa: B008
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from identity import Principal, require_scope
from staff.fleet_state_audit import default_cache_path, run_audit_async

_REPO_ROOT = Path(__file__).resolve().parents[2]
router = APIRouter(prefix="/api/staff", tags=["staff", "fleet-routines"])
v1_router = APIRouter(prefix="/api/v1/staff", tags=["staff", "fleet-routines"])
_READ = Depends(require_scope("staff.read"))


async def _audit(path: str, live: bool, write_cache: bool) -> dict[str, Any]:
    resolved = (_REPO_ROOT / path).resolve()
    if _REPO_ROOT not in resolved.parents:
        raise HTTPException(status_code=422, detail="path must stay inside the repository")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail=f"State file not found: {path}")
    return await run_audit_async(resolved, live=live, previous_path=default_cache_path(resolved), write_cache=write_cache)


def _route(r: APIRouter) -> None:
    @r.get("/fleet/state-audit", response_model_exclude_none=True)
    async def get_state_audit(
        path: Annotated[str, Query(min_length=1, max_length=500)],
        live: Annotated[bool, Query()] = True,
        write_cache: Annotated[bool, Query()] = False,
        _peer: Principal = _READ,
    ) -> dict[str, Any]:
        return await _audit(path, live, write_cache)


_route(router)
_route(v1_router)
