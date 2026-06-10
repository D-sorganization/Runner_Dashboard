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
    HttpTimeout,
)
from fastapi import APIRouter, Request
from fleet_events import (  # issue #863 — record runner/disk transitions
    FleetEventPoller,
    nodes_from_fleet_status,
)
from gh_utils import gh_api_admin
from proxy_utils import proxy_to_hub, should_proxy_fleet_to_hub
from routers.runners import run_runner_svc, runner_num_from_id, runner_svc_path
from runner_inventory import fetch_org_runners
from system_utils import (
    classify_node_offline,
    get_system_metrics_snapshot,
    resource_offline_reason,
)
from time_utils import now_ms

log = logging.getLogger("dashboard.fleet")
router = APIRouter(tags=["fleet"])

# Process-wide poller: diffs successive fleet snapshots into recorded events for
# the event-log feed (GET /api/events). Stateful (holds the previous snapshot),
# so it lives at module scope alongside the router.
_event_poller = FleetEventPoller()


def _record_fleet_events(responses: dict) -> None:
    """Feed the latest fleet snapshot into the event poller (best-effort).

    Never raises into the request path — event recording is observability, not
    a hard dependency of fleet status (Orthogonality).
    """
    try:
        nodes = nodes_from_fleet_status(responses)
        online_count = sum(1 for n in nodes if n.online)
        _event_poller.observe(
            nodes,
            ts=now_ms(),
            capacity=len(nodes) or None,
            online_count=online_count,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("Fleet event recording skipped: %s", exc)


def _runner_limit() -> int:
    """Return the hard runner capacity this dashboard is allowed to manage."""
    return max(NUM_RUNNERS, MAX_RUNNERS)


async def _fleet_control_local(action: str) -> dict:
    """Scale runners on this machine only."""
    data = await fetch_org_runners(gh_api_admin, ORG)
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
async def get_fleet_status(request: Request, exclude_pools: bool = False):
    """Get full system metrics state for all machines in the fleet network."""
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)

    responses = {}
    local_metrics = await get_system_metrics_snapshot()
    local_metrics["_role"] = "hub" if MACHINE_ROLE == "hub" else "node"

    from dashboard_config import PORT

    if PORT == 8322:
        local_pool_name = "ControlTower-HDD"
        peer_pool_name = "ControlTower-NVMe"
        peer_port = 8321
    else:
        local_pool_name = "ControlTower-NVMe"
        peer_pool_name = "ControlTower-HDD"
        peer_port = 8322

    responses[local_pool_name] = local_metrics

    async def fetch_node(name, url):
        try:
            async with httpx.AsyncClient() as client:
                target = f"{url}/api/system"
                resp = await client.get(target, timeout=HttpTimeout.PROXY_NODE_SYSTEM_S)
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

    if not exclude_pools:
        peer_url = f"http://localhost:{peer_port}/api/fleet/status?exclude_pools=true"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(peer_url, timeout=5)
                if resp.status_code == 200:
                    peer_data = resp.json()
                    for k, v in peer_data.items():
                        responses[k] = v
                else:
                    reason = classify_node_offline(status_code=resp.status_code)
                    responses[peer_pool_name] = {
                        "status": "offline",
                        "error": reason["offline_detail"],
                        **reason,
                    }
        except Exception as e:
            log.warning("Failed to query peer pool on port %d: %s", peer_port, e)
            reason = classify_node_offline(e)
            responses[peer_pool_name] = {
                "status": "offline",
                "error": reason["offline_detail"],
                **reason,
            }

    _record_fleet_events(responses)
    return responses


# /api/health is defined in backend/health.py and registered via _health_router.
