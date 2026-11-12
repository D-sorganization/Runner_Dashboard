"""Kanban GitHub ingestion API (#4319)."""

from __future__ import annotations

from typing import Any

import kanban_github_ingestion as kgi
from fastapi import APIRouter, Query

router = APIRouter(tags=["kanban"])


def _freshness_payload(freshness: dict[str, kgi.SourceFreshness]) -> dict[str, dict[str, Any]]:
    return {
        key: {
            "status": entry.status,
            "synced_at": entry.synced_at,
            "detail": entry.detail,
        }
        for key, entry in freshness.items()
    }


@router.get("/api/kanban/github")
async def get_kanban_github_board(
    owner: str = Query(..., min_length=1, max_length=100),
    repo: str = Query(..., min_length=1, max_length=100),
    force_cold: bool = Query(default=False),
) -> dict[str, Any]:
    """Return projected kanban cards from cached GitHub sources with freshness metadata."""
    result = await kgi.sync_repo_github_kanban(owner, repo, force_cold=force_cold)
    return {
        "owner": result.owner,
        "repo": result.repo,
        "last_sync": result.last_sync,
        "cache_ttl_seconds": result.cache_ttl_seconds,
        "sync_mode": result.sync_mode,
        "metrics": {
            "github_api_calls": result.metrics.github_api_calls,
            "cache_hits": result.metrics.cache_hits,
            "cache_misses": result.metrics.cache_misses,
            "api_budget_limit": result.metrics.api_budget_limit,
            "api_budget_exhausted": result.metrics.api_budget_exhausted,
        },
        "freshness": _freshness_payload(result.freshness),
        "items": [
            {
                "issue_number": row.issue_number,
                "snapshot": row.snapshot,
                "card": row.card,
            }
            for row in result.items
        ],
    }
