"""Runner group management routes.

Extracted from runners.py to keep modules under the 500-line cap.
Handles label-based runner group operations.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from gh_utils import gh_api_admin
from identity import Principal, require_scope
from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub

from .runner_helpers import run_runner_svc, runner_num_from_id, runner_sort_key

log = logging.getLogger("dashboard.runners")
router = APIRouter(tags=["runners"])


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

        from dashboard_config import ORG

        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        all_runners = data.get("runners", [])

        # Filter runners with the specified label
        grouped = [
            runner
            for runner in all_runners
            if group_label in [lbl.get("name", "") for lbl in runner.get("labels", []) if isinstance(lbl, dict)]
        ]
        grouped = sorted(grouped, key=runner_sort_key)

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
        from dashboard_config import ORG

        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        all_runners = data.get("runners", [])

        grouped = [
            runner
            for runner in all_runners
            if group_label in [lbl.get("name", "") for lbl in runner.get("labels", []) if isinstance(lbl, dict)]
        ]

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
        from dashboard_config import ORG

        data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
        all_runners = data.get("runners", [])

        grouped = [
            runner
            for runner in all_runners
            if group_label in [lbl.get("name", "") for lbl in runner.get("labels", []) if isinstance(lbl, dict)]
        ]

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
