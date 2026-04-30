"""Runner diagnostics and fleet capacity routes.

Extracted from runners.py to keep modules under the 500-line cap.
Handles diagnostics summaries, per-runner diagnostics, and fleet capacity.
"""

from __future__ import annotations

import datetime as _dt_mod
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from gh_utils import gh_api_admin
from identity import Principal, require_scope
from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub

from .runner_helpers import UTC, run_runner_svc, runner_health_check, runner_num_from_id

log = logging.getLogger("dashboard.runners")
router = APIRouter(tags=["runners"])

# Lazy-loaded system metrics snapshot function (set by server.py via runners.py)
_get_system_metrics_snapshot = None


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

        from dashboard_config import ORG

        # Keep the historical runners-router monkeypatch surface working for
        # tests and local diagnostics while this route lives in a split module.
        from . import runners as runners_router

        api_admin = getattr(runners_router, "gh_api_admin", gh_api_admin)
        data = await api_admin(f"/orgs/{ORG}/actions/runners")
        runners = data.get("runners", []) or []

        online_count = sum(1 for r in runners if r.get("status") == "online")
        offline_count = sum(1 for r in runners if r.get("status") != "online")
        busy_count = sum(1 for r in runners if r.get("busy"))
        idle_count = online_count - busy_count

        # Collect health issues
        issues = []
        for runner in runners:
            health = runner_health_check(runner)
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
        from dashboard_config import ORG

        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        runners = data.get("runners", [])

        runner = next((r for r in runners if r.get("id") == runner_id), None)

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
            "health": runner_health_check(runner),
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

        from dashboard_config import HOSTNAME, ORG

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
        from dashboard_config import ORG

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
        from dashboard_config import ORG

        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        runners = data.get("runners", [])

        runner = next((r for r in runners if r.get("id") == runner_id), None)

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
            steps.append(
                {
                    "step": "Verify status after restart",
                    "result": result_dict,
                    "status": "success" if code == 0 else "failed",
                }
            )

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
