"""GitHub API utilities for runner-dashboard."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from cache_utils import cache_get, cache_set
from dashboard_config import DEPLOYMENT_FILE, HOSTNAME, VERSION
from fastapi import HTTPException
from system_utils import BOOT_TIME, get_deployment_info, run_cmd

UTC = timezone.utc  # noqa: UP017
log = logging.getLogger("dashboard.gh_utils")


async def gh_api(endpoint: str) -> dict:
    """Call the GitHub API via gh CLI.

    Uses GH_TOKEN env var when set (required for admin:org endpoints).
    """
    code, stdout, stderr = await run_cmd(["gh", "api", endpoint])
    if code != 0:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {stderr}")
    try:
        return json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"Invalid JSON from GitHub API: {stdout}") from exc


# gh_api_admin is an alias kept for call-site clarity.
gh_api_admin = gh_api


async def gh_api_raw(endpoint: str) -> str:
    """Call the GitHub API via gh CLI and return the raw body text."""
    code, stdout, stderr = await run_cmd(["gh", "api", "-H", "Accept: application/vnd.github.raw", endpoint])
    if code != 0:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {stderr}")
    return stdout


async def get_gh_health_summary(org: str) -> dict:
    """Core health logic for GitHub runners and dashboard state."""
    try:
        # Reuse the runner cache so health checks don't add extra API calls.
        data = cache_get("runners", 25.0)
        if data is None:
            data = await gh_api_admin(f"/orgs/{org}/actions/runners")
            cache_set("runners", data)
        gh_ok = True
        runner_count = len(data.get("runners", []))
    except Exception as exc:  # noqa: BLE001
        log.warning("GitHub health check failed: %s", exc)
        gh_ok = False
        runner_count = 0

    return {
        "status": "healthy" if gh_ok else "degraded",
        "timestamp": datetime.now(UTC).isoformat(),
        "hostname": HOSTNAME,
        "github_api": "connected" if gh_ok else "unreachable",
        "runners_registered": runner_count,
        "dashboard_uptime_seconds": int(time.time() - BOOT_TIME),
        "deployment": get_deployment_info(VERSION, DEPLOYMENT_FILE),
    }
