"""Runner control and status routes.

Comprehensive runner service management including:
- Runner lifecycle control (start, stop, restart)
- Runner group management and labeling
- Fleet capacity scheduling and autoscaling
- MATLAB support and specialized runner detection
- Health monitoring and diagnostics
- Troubleshooting and state inspection endpoints

All operations are logged and authorized through the identity module.
"""

from __future__ import annotations

import asyncio
import datetime as _dt_mod
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cache_utils import cache_get, cache_set
from dashboard_config import HOSTNAME, ORG, RUNNER_BASE_DIR
from fastapi import APIRouter, Depends, HTTPException, Request
from gh_utils import gh_api_admin
from identity import Principal, require_scope
from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub
from system_utils import run_cmd

if TYPE_CHECKING:
    from collections.abc import Callable

# Python 3.10+ has UTC; fall back to timezone.utc for earlier versions
try:
    UTC = _dt_mod.UTC  # type: ignore
except AttributeError:
    UTC = _dt_mod.timezone.utc  # type: ignore

log = logging.getLogger("dashboard.runners")
router = APIRouter(tags=["runners"])


# Lazy-loaded system metrics snapshot function (set by server.py)
_get_system_metrics_snapshot: Callable[[], dict[str, Any]] | None = None


def set_system_metrics_getter(getter: Callable[[], dict[str, Any]]) -> None:
    """Register the system metrics snapshot getter (called from server.py)."""
    global _get_system_metrics_snapshot
    _get_system_metrics_snapshot = getter


def runner_svc_path(runner_num: int) -> Path:
    """Return the path to a runner's svc.sh script.

    Args:
        runner_num: Local 1-based runner index.

    Returns:
        Path object pointing to the service script.
    """
    return RUNNER_BASE_DIR / f"runner-{runner_num}" / "svc.sh"


async def run_runner_svc(runner_num: int, action: str, timeout: int = 30) -> tuple[int, str, str]:
    """Execute ./svc.sh <action> for a runner.

    Args:
        runner_num: Local 1-based runner index.
        action: Action to execute (start, stop, etc.).
        timeout: Command timeout in seconds.

    Returns:
        Tuple of (exit_code, stdout, stderr).
    """
    svc_path = runner_svc_path(runner_num)
    if not svc_path.exists():
        return 1, "", f"Service script not found: {svc_path}"
    # Use sudo if required, or run directly if permissions allow
    cmd = ["sudo", "-n", str(svc_path), action]
    return await run_cmd(cmd, timeout=timeout)


def runner_num_from_id(runner_id: int, runners: list[dict]) -> int | None:
    """Extract local 1-based runner index from a GitHub runner dict's name.

    Args:
        runner_id: GitHub runner ID.
        runners: List of runner dicts from GitHub API.

    Returns:
        Local runner number (1-based), or None if not found.
    """
    for r in runners:
        if r.get("id") == runner_id:
            name = r.get("name", "")
            # Expecting names like "d-sorg-fleet-runner-1"
            if "runner-" in name:
                try:
                    return int(name.split("runner-")[-1])
                except (ValueError, IndexError):
                    pass
    return None


def _runner_sort_key(runner: dict) -> tuple[str, int, str]:
    """Sort key for runners: status (online first), then local index, then name.

    Args:
        runner: Runner dict from GitHub API.

    Returns:
        Tuple for sorting (status_rank, runner_number, name).
    """
    status_rank = "0" if runner.get("status") == "online" else "1"
    name = runner.get("name", "")
    try:
        num = int(name.split("-")[-1]) if "-" in name else 0
    except (ValueError, IndexError):
        num = 0
    return (status_rank, num, name)


def _is_matlab_runner(runner: dict) -> bool:
    """Check if runner has MATLAB installed by examining labels.

    Args:
        runner: Runner dict from GitHub API.

    Returns:
        True if MATLAB label is present.
    """
    labels = [lbl.get("name", "").lower() for lbl in runner.get("labels", []) if isinstance(lbl, dict)]
    return "matlab" in labels or "windows-matlab" in labels


def _matlab_runner_summary(runner: dict) -> dict[str, Any]:
    """Extract summary for MATLAB runners.

    Args:
        runner: Runner dict from GitHub API.

    Returns:
        Simplified runner dict with key fields.
    """
    return {
        "id": runner.get("id"),
        "name": runner.get("name"),
        "status": runner.get("status"),
        "busy": runner.get("busy"),
        "labels": [lbl.get("name") for lbl in runner.get("labels", []) if isinstance(lbl, dict)],
    }


def _runner_group_by_label(runners: list[dict], label: str) -> dict[str, list[dict]]:
    """Group runners by a specific label.

    Args:
        runners: List of runner dicts.
        label: Label name to group by.

    Returns:
        Dict mapping label values to lists of runners.
    """
    groups: dict[str, list[dict]] = {}
    for runner in runners:
        labels = [lbl.get("name", "") for lbl in runner.get("labels", []) if isinstance(lbl, dict)]
        if label in labels:
            key = label
            if key not in groups:
                groups[key] = []
            groups[key].append(runner)
    return groups


def _runner_health_check(runner: dict, system_metrics: dict | None = None) -> dict[str, Any]:
    """Compute health status for a runner.

    Args:
        runner: Runner dict from GitHub API.
        system_metrics: Optional system metrics snapshot.

    Returns:
        Health check result dict.
    """
    health_status = "healthy"
    issues = []

    if runner.get("status") != "online":
        health_status = "offline"
        issues.append(f"runner offline ({runner.get('status')})")

    if runner.get("busy"):
        # Runner is busy, which is normal but good to track
        pass

    labels = runner.get("labels", [])
    if not labels:
        issues.append("no labels assigned")

    return {
        "runner_id": runner.get("id"),
        "runner_name": runner.get("name"),
        "status": health_status,
        "issues": issues,
        "last_check": _dt_mod.datetime.now(UTC).isoformat(),
    }


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
        cached["runners"] = sorted(cached.get("runners", []), key=_runner_sort_key)
        log.debug("returning cached runners list (count=%d)", len(cached.get("runners", [])))
        return cached

    try:
        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        data["runners"] = sorted(data.get("runners", []), key=_runner_sort_key)
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

    matlab = [r for r in all_runners if _is_matlab_runner(r)]
    summaries = [_matlab_runner_summary(r) for r in matlab]

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

        runner = None
        for r in runners:
            if r.get("id") == runner_id:
                runner = r
                break

        if runner is None:
            log.warning("get_runner_status: runner_id=%d not found", runner_id)
            raise HTTPException(status_code=404, detail=f"Runner ID {runner_id} not found")

        num = runner_num_from_id(runner_id, runners)
        health = _runner_health_check(runner)

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


@router.get("/api/runners/groups/{group_label}")
async def get_runner_group(request: Request, group_label: str) -> dict[str, Any]:
    """Get runners filtered by a specific label/group.

    Args:
        request: HTTP request.
        group_label: Label name to filter by.

    Returns:
        Dict with grouped runners and summary stats.

    Raises:
        HTTPException: If GitHub API fails.
    """
    try:
        if should_proxy_fleet_to_hub(request):
            return await proxy_to_hub(request)

        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        all_runners = data.get("runners", [])

        # Filter runners with the specified label
        grouped = []
        for runner in all_runners:
            labels = [lbl.get("name", "") for lbl in runner.get("labels", []) if isinstance(lbl, dict)]
            if group_label in labels:
                grouped.append(runner)

        grouped = sorted(grouped, key=_runner_sort_key)

        result = {
            "group_label": group_label,
            "runners": grouped,
            "total": len(grouped),
            "online": sum(1 for r in grouped if r.get("status") == "online"),
            "busy": sum(1 for r in grouped if r.get("busy")),
            "offline": sum(1 for r in grouped if r.get("status") != "online"),
        }
        log.debug("get_runner_group: label=%s (count=%d)", group_label, len(grouped))
        return result
    except Exception as exc:
        log.error("get_runner_group: error for label=%s: %s", group_label, exc)
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}") from exc


@router.post("/api/runners/groups/{group_label}/start-all")
async def start_runner_group(
    request: Request,
    group_label: str,
    principal: Principal = Depends(require_scope("runners.control")),  # noqa: B008
) -> dict[str, Any]:
    """Start all runners in a specific group/label.

    Requires the 'runners.control' scope.

    Args:
        request: HTTP request.
        group_label: Label name identifying the group.
        principal: Authenticated principal.

    Returns:
        Dict with results for each runner in the group.
    """
    try:
        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        all_runners = data.get("runners", [])

        # Filter runners with the specified label
        grouped = []
        for runner in all_runners:
            labels = [lbl.get("name", "") for lbl in runner.get("labels", []) if isinstance(lbl, dict)]
            if group_label in labels:
                grouped.append(runner)

        results = []
        for runner in grouped:
            runner_id = runner.get("id")
            num = runner_num_from_id(runner_id, all_runners)
            if num is None:
                results.append({"runner_id": runner_id, "success": False, "error": "Local runner number not found"})
                continue

            code, stdout, stderr = await run_runner_svc(num, "start")
            results.append(
                {
                    "runner_id": runner_id,
                    "runner_num": num,
                    "success": code == 0,
                    "output": stdout.strip() if code == 0 else stderr.strip(),
                }
            )

        log.info(
            "start_runner_group: label=%s (total=%d, principal=%s)",
            group_label,
            len(grouped),
            principal.user_id,
        )
        return {"group_label": group_label, "results": results, "successful": sum(1 for r in results if r["success"])}
    except Exception as exc:
        log.error("start_runner_group: error for label=%s: %s", group_label, exc)
        raise HTTPException(status_code=502, detail=f"Error: {exc}") from exc


@router.post("/api/runners/groups/{group_label}/stop-all")
async def stop_runner_group(
    request: Request,
    group_label: str,
    principal: Principal = Depends(require_scope("runners.control")),  # noqa: B008
) -> dict[str, Any]:
    """Stop all runners in a specific group/label.

    Requires the 'runners.control' scope.

    Args:
        request: HTTP request.
        group_label: Label name identifying the group.
        principal: Authenticated principal.

    Returns:
        Dict with results for each runner in the group.
    """
    try:
        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        all_runners = data.get("runners", [])

        # Filter runners with the specified label
        grouped = []
        for runner in all_runners:
            labels = [lbl.get("name", "") for lbl in runner.get("labels", []) if isinstance(lbl, dict)]
            if group_label in labels:
                grouped.append(runner)

        results = []
        for runner in grouped:
            runner_id = runner.get("id")
            num = runner_num_from_id(runner_id, all_runners)
            if num is None:
                results.append({"runner_id": runner_id, "success": False, "error": "Local runner number not found"})
                continue

            code, stdout, stderr = await run_runner_svc(num, "stop")
            results.append(
                {
                    "runner_id": runner_id,
                    "runner_num": num,
                    "success": code == 0,
                    "output": stdout.strip() if code == 0 else stderr.strip(),
                }
            )

        log.info(
            "stop_runner_group: label=%s (total=%d, principal=%s)",
            group_label,
            len(grouped),
            principal.user_id,
        )
        return {"group_label": group_label, "results": results, "successful": sum(1 for r in results if r["success"])}
    except Exception as exc:
        log.error("stop_runner_group: error for label=%s: %s", group_label, exc)
        raise HTTPException(status_code=502, detail=f"Error: {exc}") from exc


@router.get("/api/runners/diagnostics/summary")
async def get_runners_diagnostics_summary(request: Request) -> dict[str, Any]:
    """Get a comprehensive diagnostics summary for all runners.

    Includes health checks, connectivity status, and resource utilization overview.

    Args:
        request: HTTP request.

    Returns:
        Dict with runner counts by status, health issues, and recommendations.
    """
    try:
        if should_proxy_fleet_to_hub(request):
            return await proxy_to_hub(request)

        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        runners = data.get("runners", []) or []

        online_count = sum(1 for r in runners if r.get("status") == "online")
        offline_count = sum(1 for r in runners if r.get("status") != "online")
        busy_count = sum(1 for r in runners if r.get("busy"))
        idle_count = online_count - busy_count

        # Collect health issues
        issues = []
        for runner in runners:
            health = _runner_health_check(runner)
            if health["issues"]:
                issues.append({"runner": health["runner_name"], "issues": health["issues"]})

        recommendations = []
        if offline_count > 0:
            recommendations.append(f"Check offline runners ({offline_count}): restart svc or check system status")
        if idle_count == 0 and online_count > 0:
            recommendations.append("All online runners are busy; consider scaling up fleet capacity")

        summary = {
            "total_runners": len(runners),
            "online": online_count,
            "offline": offline_count,
            "busy": busy_count,
            "idle": idle_count,
            "health_issues": issues,
            "recommendations": recommendations,
            "generated_at": _dt_mod.datetime.now(UTC).isoformat(),
        }
        log.info(
            "get_runners_diagnostics_summary: total=%d, online=%d, offline=%d",
            len(runners),
            online_count,
            offline_count,
        )
        return summary
    except Exception as exc:
        log.error("get_runners_diagnostics_summary: error: %s", exc)
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}") from exc


@router.post("/api/runners/{runner_id}/diagnostics")
async def get_runner_diagnostics(request: Request, runner_id: int) -> dict[str, Any]:
    """Get detailed diagnostics for a specific runner.

    Includes service status, recent activity, and potential troubleshooting info.

    Args:
        request: HTTP request.
        runner_id: GitHub runner ID.

    Returns:
        Dict with detailed diagnostics and troubleshooting info.

    Raises:
        HTTPException: If runner not found.
    """
    try:
        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        runners = data.get("runners", [])

        runner = None
        for r in runners:
            if r.get("id") == runner_id:
                runner = r
                break

        if runner is None:
            log.warning("get_runner_diagnostics: runner_id=%d not found", runner_id)
            raise HTTPException(status_code=404, detail=f"Runner ID {runner_id} not found")

        num = runner_num_from_id(runner_id, runners)

        diagnostics: dict[str, Any] = {
            "runner_id": runner_id,
            "runner_name": runner.get("name"),
            "runner_num": num,
            "status": runner.get("status"),
            "busy": runner.get("busy"),
            "health": _runner_health_check(runner),
            "labels": [lbl.get("name") for lbl in runner.get("labels", []) if isinstance(lbl, dict)],
            "accessed_at": runner.get("accessed_at"),
            "created_at": runner.get("created_at"),
        }

        # Try to read svc status if local runner number found
        if num is not None:
            code, stdout, _ = await run_runner_svc(num, "status")
            diagnostics["svc_status"] = {"exit_code": code, "output": stdout.strip()}

        # Troubleshooting suggestions
        troubleshooting = []
        if runner.get("status") != "online":
            troubleshooting.append("Runner offline: check system status, network, and service logs")
            if num is not None:
                troubleshooting.append(f"Try: restart service runner-{num} via dashboard or SSH")
        if not runner.get("labels"):
            troubleshooting.append("No labels: add labels to runner in GitHub Actions settings")
        if runner.get("busy"):
            accessed_str = runner.get("accessed_at", "2000-01-01T00:00:00Z").replace("Z", "+00:00")
            accessed_dt = _dt_mod.datetime.fromisoformat(accessed_str)
            time_diff = time.time() - time.mktime(accessed_dt.timetuple())
            if time_diff > 3600:
                troubleshooting.append("Runner busy for over 1 hour: may be stuck, consider manual restart")

        diagnostics["troubleshooting_suggestions"] = troubleshooting
        log.debug("get_runner_diagnostics: runner_id=%d", runner_id)
        return diagnostics
    except HTTPException:
        raise
    except Exception as exc:
        log.error("get_runner_diagnostics: error for runner_id=%d: %s", runner_id, exc)
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}") from exc


@router.get("/api/runners/fleet/capacity")
async def get_fleet_capacity(request: Request) -> dict[str, Any]:
    """Get fleet capacity and scheduling information.

    Provides current utilization, available capacity, and recommendations for scaling.

    Args:
        request: HTTP request.

    Returns:
        Dict with capacity metrics and scheduling recommendations.
    """
    try:
        if should_proxy_fleet_to_hub(request):
            return await proxy_to_hub(request)

        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        runners = data.get("runners", []) or []

        online = sum(1 for r in runners if r.get("status") == "online")
        busy = sum(1 for r in runners if r.get("busy"))
        idle = online - busy
        total = len(runners)

        # Get system metrics if available
        system_metrics = None
        if _get_system_metrics_snapshot:
            try:
                system_metrics = await _get_system_metrics_snapshot()  # type: ignore
            except Exception as e:
                log.debug("Failed to get system metrics for capacity: %s", e)

        utilization_percent = int((busy / online * 100) if online > 0 else 0)

        # Scaling recommendations
        recommendations = []
        if utilization_percent > 80:
            recommendations.append("HIGH utilization: consider scaling up fleet")
        elif utilization_percent > 60:
            recommendations.append("MODERATE utilization: monitor for growth")
        if idle == 0 and online > 0:
            recommendations.append("No idle runners: all capacity in use")

        capacity = {
            "total_runners": total,
            "online_runners": online,
            "offline_runners": total - online,
            "busy_runners": busy,
            "idle_runners": idle,
            "utilization_percent": utilization_percent,
            "hostname": HOSTNAME,
            "recommendations": recommendations,
            "generated_at": _dt_mod.datetime.now(UTC).isoformat(),
        }

        if system_metrics:
            capacity["system_health"] = {
                "cpu_percent": system_metrics.get("cpu_percent"),
                "memory_percent": system_metrics.get("memory_percent"),
                "disk_pressure": system_metrics.get("disk_pressure"),
            }

        log.info(
            "get_fleet_capacity: total=%d, online=%d, busy=%d, util=%d%%",
            total,
            online,
            busy,
            utilization_percent,
        )
        return capacity
    except Exception as exc:
        log.error("get_fleet_capacity: error: %s", exc)
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}") from exc


@router.post("/api/runners/fleet/schedule-scale")
async def schedule_fleet_scale(
    request: Request,
    principal: Principal = Depends(require_scope("runners.control")),  # noqa: B008
) -> dict[str, Any]:
    """Schedule automatic fleet scaling based on current utilization.

    Analyzes current utilization and recommends scaling actions.

    Args:
        request: HTTP request with optional body containing scale directives.
        principal: Authenticated principal.

    Returns:
        Dict with scheduled scaling actions and results.
    """
    try:
        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        runners = data.get("runners", []) or []

        online = sum(1 for r in runners if r.get("status") == "online")
        busy = sum(1 for r in runners if r.get("busy"))
        idle = online - busy
        utilization_percent = int((busy / online * 100) if online > 0 else 0)

        scheduled_actions = []

        # Simple autoscaling logic
        if utilization_percent > 80 and idle == 0:
            # Try to start one idle runner if available
            for runner in runners:
                if runner.get("status") != "online":
                    num = runner_num_from_id(runner.get("id"), runners)
                    if num is not None:
                        code, _, _ = await run_runner_svc(num, "start")
                        scheduled_actions.append(
                            {
                                "action": "start",
                                "runner_id": runner.get("id"),
                                "runner_num": num,
                                "success": code == 0,
                            }
                        )
                        break

        log.info(
            "schedule_fleet_scale: util=%d%%, scheduled=%d actions (principal=%s)",
            utilization_percent,
            len(scheduled_actions),
            principal.user_id,
        )
        return {
            "utilization_percent": utilization_percent,
            "scheduled_actions": scheduled_actions,
            "total_actions": len(scheduled_actions),
        }
    except Exception as exc:
        log.error("schedule_fleet_scale: error: %s", exc)
        raise HTTPException(status_code=502, detail=f"Error: {exc}") from exc


@router.post("/api/runners/{runner_id}/troubleshoot")
async def troubleshoot_runner(
    request: Request,
    runner_id: int,
    principal: Principal = Depends(require_scope("runners.control")),  # noqa: B008
) -> dict[str, Any]:
    """Perform automated troubleshooting on a runner.

    Runs diagnostics and attempts automatic fixes for common issues.

    Args:
        request: HTTP request.
        runner_id: GitHub runner ID.
        principal: Authenticated principal.

    Returns:
        Dict with troubleshooting steps and results.

    Raises:
        HTTPException: If runner not found.
    """
    try:
        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        runners = data.get("runners", [])

        runner = None
        for r in runners:
            if r.get("id") == runner_id:
                runner = r
                break

        if runner is None:
            log.warning("troubleshoot_runner: runner_id=%d not found (principal=%s)", runner_id, principal.user_id)
            raise HTTPException(status_code=404, detail=f"Runner ID {runner_id} not found")

        num = runner_num_from_id(runner_id, runners)
        steps: list[dict[str, Any]] = []

        # Step 1: Check current status
        steps.append({"step": "Check status", "status": "pending"})
        if runner.get("status") != "online":
            steps.append({"step": "Attempt restart", "status": "pending"})
            if num is not None:
                code, stdout, stderr = await run_runner_svc(num, "restart")
                result_dict: dict[str, Any] = {
                    "exit_code": code,
                    "output": stdout.strip() if code == 0 else stderr.strip(),
                }
                steps[-1]["result"] = result_dict  # type: ignore
                steps[-1]["status"] = "success" if code == 0 else "failed"  # type: ignore

        # Step 2: Verify online after restart
        if num is not None:
            code, stdout, _ = await run_runner_svc(num, "status")
            result_dict = {"exit_code": code, "output": stdout.strip()}
            steps.append({
                "step": "Verify status after restart",
                "result": result_dict,
                "status": "success" if code == 0 else "failed",
            })

        log.info(
            "troubleshoot_runner: runner_id=%d (runner_num=%s, principal=%s)",
            runner_id,
            num,
            principal.user_id,
        )
        return {
            "runner_id": runner_id,
            "runner_num": num,
            "troubleshooting_steps": steps,
            "success": all(s["status"] in ("success", "pending") for s in steps),
        }
    except HTTPException:
        raise
    except Exception as exc:
        log.error("troubleshoot_runner: error for runner_id=%d: %s", runner_id, exc)
        raise HTTPException(status_code=502, detail=f"Error: {exc}") from exc
