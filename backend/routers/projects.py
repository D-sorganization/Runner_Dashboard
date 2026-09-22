"""Projects tab API (issue #1199, epic #1192): per-repo charter, status and steward runs.

``GET /api/projects``          — every repository in ``config/projects.json``.
``GET /api/projects/{repo}``   — one configured repository.

Both are reads gated by ``require_fleet_peer`` like the fleet routes. There is
no mutation here: "Run steward now" on the Projects page posts to the existing
``POST /api/staff/project-steward/run`` (orchestrator peer, CSRF header).
Overview assembly and caching live in ``projects.service``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from identity import require_fleet_peer
from projects.service import CACHE_TTL_SECONDS, configured_repos, project_overview

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
async def list_projects(_peer: str = Depends(require_fleet_peer)) -> dict[str, Any]:
    """All configured repositories, in config order. A bad repo reports ``error``; never 5xx."""
    repos = configured_repos()
    projects = await asyncio.gather(*(project_overview(repo) for repo in repos))
    return {"projects": list(projects), "count": len(projects), "cache_ttl_seconds": CACHE_TTL_SECONDS}


@router.get("/{repo}")
async def get_project(repo: str, _peer: str = Depends(require_fleet_peer)) -> dict[str, Any]:
    """One configured repository. Unknown names are 404 so the route never fetches arbitrary repos."""
    if repo not in configured_repos():
        raise HTTPException(status_code=404, detail=f"repo {repo!r} is not in config/projects.json")
    return await project_overview(repo)
