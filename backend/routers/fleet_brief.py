"""GET /api/v1/fleet/brief — single cached board a messenger bot can relay verbatim (issue #3314)."""

# ruff: noqa: B008
from __future__ import annotations

import hashlib
import json
from typing import Any

from cache_utils import cache_get, cache_set
from coordination.brief import BRIEF_CACHE_TTL_S, build_fleet_brief, render_markdown
from fastapi import APIRouter, Depends, Query, Request, Response
from identity import require_fleet_peer

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet-brief"])


def _cache_key(repo: str | None, limit: int) -> str:
    return f"fleet:brief:v1:{repo or 'all'}:{limit}"


def _etag(brief: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(brief, sort_keys=True, default=str).encode()).hexdigest()
    return f'"{digest[:32]}"'


@router.get("/brief")
async def get_fleet_brief(
    request: Request,
    response: Response,
    repo: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
    format: str = Query(default="json", pattern="^(json|markdown)$"),
    _peer: str = Depends(require_fleet_peer),
) -> Any:
    key = _cache_key(repo, limit)
    brief = cache_get(key, BRIEF_CACHE_TTL_S)
    if brief is None:
        brief = await build_fleet_brief(repo, limit=limit)
        cache_set(key, brief)

    etag = _etag(brief)
    response.headers["ETag"] = etag
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    if format == "markdown":
        return Response(content=render_markdown(brief), media_type="text/markdown", headers={"ETag": etag})
    return brief
