"""GitHub API utilities for runner-dashboard."""

from __future__ import annotations

import json

from fastapi import HTTPException
from system_utils import run_cmd


async def gh_api(endpoint: str) -> dict:
    """Call the GitHub API via gh CLI."""
    code, stdout, stderr = await run_cmd(["gh", "api", endpoint])
    if code != 0:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {stderr}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"Invalid JSON from GitHub API: {stdout}") from exc


async def gh_api_admin(endpoint: str) -> dict:
    """Call the GitHub API via gh CLI (alias for consistency)."""
    return await gh_api(endpoint)
