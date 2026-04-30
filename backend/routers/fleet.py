"""Fleet management routes."""

from __future__ import annotations

import asyncio
import logging

import httpx
from dashboard_config import (
    FLEET_NODES,
    HOSTNAME,
    MACHINE_ROLE,
    MAX_RUNNERS,
    NUM_RUNNERS,
    ORG,
)
from fastapi import APIRouter, Request
from gh_utils import gh_api_admin
from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub
from routers.runners import run_runner_svc, runner_num_from_id, runner_svc_path
from system_utils import (
    classify_node_offline,
    get_system_metrics_snapshot,
    resource_offline_reason,
)

log = logging.getLogger("dashboard.fleet")
router = APIRouter(tags=["fleet"])


def _runner_limit() -> int:
    """Return the hard runner capacity this dashboard is allowed to manage."""
    return max(NUM_RUNNERS, MAX_RUNNERS)


async def _fleet_control_local(action: str) -> dict:
    """Scale runners on this machine only."""
    data = await gh_api_admin(f"/orgs/{ORG}/actions/runners")
    runners = data.get("runners", [])
    results = []

    log.info("Local runner control on %s: %s", HOSTNAME, action)

    if action == "all-up":
        for i in range(1, _runner_limit() + 1):
            if runner_svc_path(i).exists():
                code, _, _ = await run_runner_svc(i, "start")
                results.append({"runner": i, "action": "start", "success": code == 0})

    elif action == "all-down":
        for i in range(1, _runner_limit() + 1):
            if runner_svc_path(i).exists():
                code, _, _ = await run_runner_svc(i, "stop")
                results.append({"runner": i, "action": "stop", "success": code == 0})

    elif action == "up":
        online_nums = set()
        for r in runners:
            if r["status"] == "online":
                num = runner_num_from_id(r["id"], runners)
                if num:
                    online_nums.add(num)
        for i in range(1, _runner_limit() + 1):
            if i not in online_nums:
                if runner_svc_path(i).exists():
                    code, _, _ = await run_runner_svc(i, "start")
                    results.append({"runner": i, "action": "start", "success": code == 0})
                    break

    elif action == "down":
        idle_runners = []
        for r in runners:
            if r["status"] == "online" and not r.get("busy"):
                num = runner_num_from_id(r["id"], runners)
                if num:
                    idle_runners.append(num)
        if idle_runners:
            target = max(idle_runners)
            if runner_svc_path(target).exists():
                code, _, _ = await run_runner_svc(target, "stop")
                results.append({"runner": target, "action": "stop", "success": code == 0})

    return {"results": results, "hostname": HOSTNAME}


# Runner control routes are defined in routers/runners.py


@router.get("/api/fleet/status")
async def get_fleet_status(request: Request):
    """Get full system metrics state for all machines in the fleet network."""
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    responses = {}
    local_metrics = await get_system_metrics_snapshot()
    local_metrics["_role"] = "hub" if MACHINE_ROLE == "hub" else "node"
    responses[HOSTNAME] = local_metrics

    async def fetch_node(name, url):
        try:
            async with httpx.AsyncClient() as client:
                target = f"{url}/api/system"
                resp = await client.get(target, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    data["_role"] = "node"
                    res_reason = resource_offline_reason(data)
                    if res_reason:
                        data.update(res_reason)
                    return name, data

                reason = classify_node_offline(status_code=resp.status_code)
                return name, {
                    "status": "offline",
                    "error": reason["offline_detail"],
                    **reason,
                }
        except Exception as e:
            reason = classify_node_offline(e)
            return name, {
                "status": "offline",
                "error": reason["offline_detail"],
                **reason,
            }

    if FLEET_NODES:
        results = await asyncio.gather(*[fetch_node(n, u) for n, u in FLEET_NODES.items()])
        for name, data in results:
            responses[name] = data

    return responses


# /api/health is defined in backend/health.py and registered via _health_router.
