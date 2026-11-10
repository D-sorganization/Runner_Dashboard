"""GET /api/v1/fleet/state-audit — Barb state table audit (#3640 API slice)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from cache_utils import cache_get, cache_set
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from identity import require_fleet_peer
from staff.fleet_state_audit import default_cache_path, run_audit

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet-state-audit"])

_REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_STATE_REL = Path("docs/agents/rd-barb-state.md")
CACHE_TTL_S = 300


def _cache_key(path: Path, live: bool, write_cache: bool) -> str:
    return f"fleet:state-audit:v1:{path}:{int(live)}:{int(write_cache)}"


def _etag(report: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(report, sort_keys=True, default=str).encode()).hexdigest()
    return f'"{digest[:32]}"'


def resolve_canonical_state_path(path: str) -> Path:
    """Only the committed Barb state table may be audited through this API."""
    requested = Path((path or "").strip() or str(CANONICAL_STATE_REL))
    if requested != CANONICAL_STATE_REL:
        raise HTTPException(
            status_code=400,
            detail=f"path must be {CANONICAL_STATE_REL.as_posix()}",
        )
    resolved = (_REPO_ROOT / CANONICAL_STATE_REL).resolve()
    try:
        resolved.relative_to(_REPO_ROOT.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path escapes repository root") from exc
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="canonical state file missing")
    return resolved


@router.get("/state-audit")
async def get_fleet_state_audit(
    request: Request,
    response: Response,
    path: str = Query(default="docs/agents/rd-barb-state.md", max_length=300),
    live: bool = Query(default=True),
    write_cache: bool = Query(default=False),
    _peer: str = Depends(require_fleet_peer),
) -> Any:
    """Run or return a cached Barb state audit report."""
    state_path = resolve_canonical_state_path(path)
    key = _cache_key(state_path, live, write_cache)
    cached = cache_get(key, CACHE_TTL_S) if live else None
    if isinstance(cached, dict):
        report = cached
    else:
        import anyio

        audit_write_cache = write_cache
        cache_target = default_cache_path(state_path)

        report = await anyio.to_thread.run_sync(
            lambda: run_audit(
                state_path,
                live=live,
                previous_path=cache_target,
                write_cache=audit_write_cache,
            )
        )
        if live:
            cache_set(key, report)
    etag = _etag(report)
    response.headers["ETag"] = etag
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return report
