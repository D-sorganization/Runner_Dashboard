"""Projects tab API (issue #1199, epic #1192): per-repo charter, status and steward runs.

``GET /api/projects``            — every repository in ``config/projects.json``, P0 first, plus a summary.
``GET /api/projects/priorities`` — the owner's tiers (Repository_Management config/project_priorities.yaml).
``GET /api/projects/untracked``  — the fleet-curator worklist: open items no charter feature accounts
                                   for, charter-less repos and org repos missing from the config.
``GET /api/projects/{repo}``     — one configured repository.

Both are reads gated by ``require_fleet_peer`` like the fleet routes. There is
no mutation here: "Run steward now" on the Projects page posts to the existing
``POST /api/staff/project-steward/run`` (orchestrator peer, CSRF header).
Overview assembly and caching live in ``projects.service``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from identity import require_fleet_peer
from projects import rollup
from projects.priorities import PRIORITIES_PATH, PRIORITIES_REPO, TIER_MEANING, TIERS
from projects.service import (
    CACHE_TTL_SECONDS,
    configured_repos,
    fetch_org_repos,
    fleet_overview,
    load_priorities,
    project_overview,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
async def list_projects(_peer: str = Depends(require_fleet_peer)) -> dict[str, Any]:
    """All configured repositories, P0 first (config order within a tier). Never 5xx for one repo."""
    body = await fleet_overview(configured_repos())
    return {**body, "count": len(body["projects"]), "cache_ttl_seconds": CACHE_TTL_SECONDS}


@router.get("/priorities")
async def get_priorities(_peer: str = Depends(require_fleet_peer)) -> dict[str, Any]:
    """The tier vocabulary and the owner's current ranking."""
    prio, error = await load_priorities()
    body: dict[str, Any] = {
        "tiers": [{"tier": tier, "meaning": TIER_MEANING[tier]} for tier in TIERS],
        "projects": {repo: p.to_dict() for repo, p in prio.items()},
        "source": f"{PRIORITIES_REPO}/{PRIORITIES_PATH}",
    }
    if error:
        body["error"] = error
    return body


@router.get("/untracked")
async def get_untracked(_peer: str = Depends(require_fleet_peer)) -> dict[str, Any]:
    """Fleet-curator worklist. An org-listing failure leaves ``unregistered_repos`` empty with ``org_error``."""
    body = await fleet_overview(configured_repos())
    org_error = None
    try:
        org_repos = await fetch_org_repos()
    except HTTPException as exc:
        org_repos, org_error = [], f"github: {exc.detail}"
    report = rollup.untracked_report(body["projects"], org_repos)
    if org_error:
        report["org_error"] = org_error
    return report


@router.get("/{repo}")
async def get_project(repo: str, _peer: str = Depends(require_fleet_peer)) -> dict[str, Any]:
    """One configured repository. Unknown names are 404 so the route never fetches arbitrary repos."""
    if repo not in configured_repos():
        raise HTTPException(status_code=404, detail=f"repo {repo!r} is not in config/projects.json")
    return await project_overview(repo)
