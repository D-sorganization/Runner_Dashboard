"""Runner control and status routes.

Core runner lifecycle management:
- Runner listing and status queries
- Runner lifecycle control (start, stop, restart)
- MATLAB runner health

Extended functionality is split into sub-modules:
- runner_groups: label-based group operations
- runner_diagnostics: diagnostics, fleet capacity, autoscaling, troubleshooting
- runner_helpers: shared utility functions

All operations are logged and authorized through the identity module.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from cache_utils import cache_get, cache_set
from dashboard_config import ORG
from fastapi import APIRouter, Depends, HTTPException, Request
from gh_utils import gh_api_admin
from identity import Principal, require_scope
from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub

from .runner_helpers import (
    is_matlab_runner,
    matlab_runner_summary,
    run_runner_svc,
    runner_health_check,
    runner_num_from_id,
    runner_sort_key,
    runner_svc_path,
)

# Re-export private helper names expected by existing test suite
_is_matlab_runner = is_matlab_runner
_runner_sort_key = runner_sort_key

__all__ = [
    "run_runner_svc",
    "runner_num_from_id",
    "runner_svc_path",
    "_is_matlab_runner",
    "_runner_sort_key",
]

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger("dashboard.runners")
router = APIRouter(tags=["runners"])


# Lazy-loaded system metrics snapshot function (set by server.py)
_get_system_metrics_snapshot: Callable[[], dict[str, Any]] | None = None


def set_system_metrics_getter(getter: Callable[[], dict[str, Any]]) -> None:
    """Register the system metrics snapshot getter (called from server.py)."""
    global _get_system_metrics_snapshot
    _get_system_metrics_snapshot = getter


@router.get("/api/runners")
async def get_runners(request: Request) -> dict[str, Any]:
    """Get all org runners with their status.

    This endpoint lists all GitHub Actions runners configured for the organization,
    sorted by status (online first) and runner number.

    Args:
        request: HTTP request (used for proxy detection).

    Returns:
        Dict with 'runners' list and total count.

    Raises:
        HTTPException: If GitHub API fails.
    """
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    cached = cache_get("runners", 60.0)
    if cached is not None:
        cached["runners"] = sorted(cached.get("runners", []), key=runner_sort_key)
        log.debug("returning cached runners list (count=%d)", len(cached.get("runners", [])))
        return cached

    try:
        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        data["runners"] = sorted(data.get("runners", []), key=runner_sort_key)
        cache_set("runners", data)
        log.info("fetched runners list from GitHub API (count=%d)", len(data.get("runners", [])))
        return data
    except Exception as exc:
        log.error("failed to fetch runners: %s", exc)
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}") from exc


@router.get("/api/runners/matlab")
async def get_matlab_runner_health(request: Request) -> dict[str, Any]:
    """Surface Windows MATLAB runner health for the dashboard.

    Returns a summary of MATLAB-capable runners and their current status.

    Args:
        request: HTTP request (used for proxy detection).

    Returns:
        Dict with runner list, totals, and generated timestamp.
    """
    import datetime as _dt_mod

    from .runner_helpers import UTC

    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    cached = cache_get("matlab_runner_health", 45.0)
    if cached is not None:
        log.debug("returning cached MATLAB runner health")
        return cached

    try:
        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        all_runners = data.get("runners", []) or []
    except Exception as exc:
        log.warning("failed to fetch runners for MATLAB health check: %s", exc)
        all_runners = []

    matlab = [r for r in all_runners if is_matlab_runner(r)]
    summaries = [matlab_runner_summary(r) for r in matlab]

    res = {
        "runners": summaries,
        "total": len(summaries),
        "online": sum(1 for r in summaries if r["status"] == "online"),
        "busy": sum(1 for r in summaries if r["busy"]),
        "offline": sum(1 for r in summaries if r["status"] != "online"),
        "generated_at": _dt_mod.datetime.now(UTC).isoformat(),
    }
    cache_set("matlab_runner_health", res)
    log.info("computed MATLAB runner health (total=%d, online=%d)", len(summaries), res["online"])
    return res


@router.post("/api/runners/{runner_id}/start")
async def start_runner(
    request: Request,
    runner_id: int,
    principal: Principal = Depends(require_scope("runners.control")),  # noqa: B008
) -> dict[str, Any]:
    """Start a specific runner's service.

    Requires the 'runners.control' scope.

    Args:
        request: HTTP request.
        runner_id: GitHub runner ID.
        principal: Authenticated principal.

    Returns:
        Dict with status, runner number, and command output.

    Raises:
        HTTPException: If runner not found or start fails.
    """
    try:
        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        runners = data.get("runners", [])
        num = runner_num_from_id(runner_id, runners)

        if num is None:
            log.warning("start_runner: runner_id=%d not found locally (principal=%s)", runner_id, principal.user_id)
            raise HTTPException(status_code=404, detail=f"Runner ID {runner_id} not found locally")

        code, stdout, stderr = await run_runner_svc(num, "start")
        if code != 0:
            log.error("start_runner: failed for runner_num=%d: %s (principal=%s)", num, stderr, principal.user_id)
            raise HTTPException(status_code=500, detail=f"Failed to start runner {num}: {stderr}")

        log.info("start_runner: started runner_num=%d (runner_id=%d, principal=%s)", num, runner_id, principal.user_id)
        return {"status": "started", "runner": num, "output": stdout.strip()}
    except HTTPException:
        raise
    except Exception as exc:
        log.error("start_runner: unexpected error for runner_id=%d: %s", runner_id, exc)
        raise HTTPException(status_code=500, detail=f"Internal error: {exc}") from exc


@router.post("/api/runners/{runner_id}/stop")
async def stop_runner(
    request: Request,
    runner_id: int,
    principal: Principal = Depends(require_scope("runners.control")),  # noqa: B008
) -> dict[str, Any]:
    """Stop a specific runner's service.

    Requires the 'runners.control' scope.

    Args:
        request: HTTP request.
        runner_id: GitHub runner ID.
        principal: Authenticated principal.

    Returns:
        Dict with status, runner number, and command output.

    Raises:
        HTTPException: If runner not found or stop fails.
    """
    try:
        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        runners = data.get("runners", [])
        num = runner_num_from_id(runner_id, runners)

        if num is None:
            log.warning("stop_runner: runner_id=%d not found locally (principal=%s)", runner_id, principal.user_id)
            raise HTTPException(status_code=404, detail=f"Runner ID {runner_id} not found locally")

        code, stdout, stderr = await run_runner_svc(num, "stop")
        if code != 0:
            log.error("stop_runner: failed for runner_num=%d: %s (principal=%s)", num, stderr, principal.user_id)
            raise HTTPException(status_code=500, detail=f"Failed to stop runner {num}: {stderr}")

        log.info("stop_runner: stopped runner_num=%d (runner_id=%d, principal=%s)", num, runner_id, principal.user_id)
        return {"status": "stopped", "runner": num, "output": stdout.strip()}
    except HTTPException:
        raise
    except Exception as exc:
        log.error("stop_runner: unexpected error for runner_id=%d: %s", runner_id, exc)
        raise HTTPException(status_code=500, detail=f"Internal error: {exc}") from exc


@router.post("/api/runners/{runner_id}/restart")
async def restart_runner(
    request: Request,
    runner_id: int,
    principal: Principal = Depends(require_scope("runners.control")),  # noqa: B008
) -> dict[str, Any]:
    """Restart a specific runner's service.

    Performs a stop-then-start sequence with a brief delay between.

    Args:
        request: HTTP request.
        runner_id: GitHub runner ID.
        principal: Authenticated principal.

    Returns:
        Dict with status and results of both operations.

    Raises:
        HTTPException: If runner not found or restart fails.
    """
    try:
        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        runners = data.get("runners", [])
        num = runner_num_from_id(runner_id, runners)

        if num is None:
            log.warning("restart_runner: runner_id=%d not found locally (principal=%s)", runner_id, principal.user_id)
            raise HTTPException(status_code=404, detail=f"Runner ID {runner_id} not found locally")

        # Stop
        stop_code, stop_stdout, stop_stderr = await run_runner_svc(num, "stop")
        if stop_code != 0:
            log.error("restart_runner: stop failed for runner_num=%d: %s", num, stop_stderr)
            raise HTTPException(status_code=500, detail=f"Failed to stop runner {num}: {stop_stderr}")

        # Brief delay
        await asyncio.sleep(1)

        # Start
        start_code, start_stdout, start_stderr = await run_runner_svc(num, "start")
        if start_code != 0:
            log.error("restart_runner: start failed for runner_num=%d: %s", num, start_stderr)
            raise HTTPException(status_code=500, detail=f"Failed to start runner {num}: {start_stderr}")

        log.info(
            "restart_runner: restarted runner_num=%d (runner_id=%d, principal=%s)",
            num,
            runner_id,
            principal.user_id,
        )
        return {
            "status": "restarted",
            "runner": num,
            "stop_output": stop_stdout.strip(),
            "start_output": start_stdout.strip(),
        }
    except HTTPException:
        raise
    except Exception as exc:
        log.error("restart_runner: unexpected error for runner_id=%d: %s", runner_id, exc)
        raise HTTPException(status_code=500, detail=f"Internal error: {exc}") from exc


@router.get("/api/runners/{runner_id}/status")
async def get_runner_status(request: Request, runner_id: int) -> dict[str, Any]:
    """Get detailed status and health information for a specific runner.

    Args:
        request: HTTP request.
        runner_id: GitHub runner ID.

    Returns:
        Dict with runner details, status, and health check results.

    Raises:
        HTTPException: If runner not found.
    """
    try:
        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        runners = data.get("runners", [])

        runner = next((r for r in runners if r.get("id") == runner_id), None)

        if runner is None:
            log.warning("get_runner_status: runner_id=%d not found", runner_id)
            raise HTTPException(status_code=404, detail=f"Runner ID {runner_id} not found")

        num = runner_num_from_id(runner_id, runners)
        health = runner_health_check(runner)

        response = {
            "id": runner.get("id"),
            "name": runner.get("name"),
            "status": runner.get("status"),
            "busy": runner.get("busy"),
            "labels": [lbl.get("name") for lbl in runner.get("labels", []) if isinstance(lbl, dict)],
            "local_runner_number": num,
            "health": health,
            "os": runner.get("os"),
            "total_actions_current": runner.get("total_actions_current", 0),
            "accessed_at": runner.get("accessed_at"),
            "created_at": runner.get("created_at"),
        }
        log.debug("get_runner_status: retrieved for runner_id=%d", runner_id)
        return response
    except HTTPException:
        raise
    except Exception as exc:
        log.error("get_runner_status: error for runner_id=%d: %s", runner_id, exc)
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}") from exc
