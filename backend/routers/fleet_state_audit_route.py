"""GET /api/v1/fleet/state-audit — Barb state table audit (#3640 API slice)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from cache_utils import cache_get, cache_set
from fastapi import APIRouter, Depends, Query, Request, Response
from identity import require_fleet_peer
from staff.fleet_state_audit import default_cache_path, run_audit

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet-state-audit"])

DEFAULT_STATE_PATH = Path("docs/agents/rd-barb-state.md")
CACHE_TTL_S = 300


def _cache_key(path: Path, live: bool, write_cache: bool) -> str:
    return f"fleet:state-audit:v1:{path}:{int(live)}:{int(write_cache)}"


def _etag(report: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(report, sort_keys=True, default=str).encode()).hexdigest()
    return f'"{digest[:32]}"'


@router.get("/state-audit")
async def get_fleet_state_audit(
    request: Request,
    response: Response,
    path: str = Query(default="docs/agents/rd-barb-state.md", max_length=300),
    live: bool = Query(default=True),
    write_cache: bool = Query(default=False),
    _peer: str = Depends(require_fleet_peer),
) -> dict[str, Any]:
    """Run or return a cached Barb state audit report."""
    state_path = Path(path)
    if not state_path.is_file():
        state_path = DEFAULT_STATE_PATH
    key = _cache_key(state_path, live, write_cache)
    report = cache_get(key, CACHE_TTL_S) if live else None
    if report is None:
        import anyio

        report = await anyio.to_thread.run_sync(
            lambda: run_audit(
                state_path,
                live=live,
                previous_path=default_cache_path(state_path),
                write_cache=write_cache,
            )
        )
        if live:
            cache_set(key, report)
    etag = _etag(report)
    response.headers["ETag"] = etag
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})  # type: ignore[return-value]
    return report
