"""Managed AGENTS block drift report against Repository_Management (SC-F3)."""

# ruff: noqa: B008
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from identity import Principal, require_scope

router = APIRouter(prefix="/context", tags=["staff-context"])


@router.get("/agents-sync")
async def context_agents_sync_v1(
    _peer: Principal = Depends(require_scope("staff.read")),
) -> dict[str, Any]:
    """Compare published managed AGENTS blocks with the Repository_Management source."""
    from cache_utils import cache_get, cache_set
    from projects.service import configured_repos, fetch_repo_file
    from staff.agents_sync import build_agents_sync_report

    cached = cache_get("context:agents-sync", 600)
    if cached is not None:
        return cached

    async def fetch_file(repo: str, path: str) -> str | None:
        return await fetch_repo_file(repo, path)

    report = await build_agents_sync_report(fetch_file, configured_repos())
    cache_set("context:agents-sync", report)
    return report
