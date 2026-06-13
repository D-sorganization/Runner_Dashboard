"""Fleet management routes."""

from __future__ import annotations

import asyncio
import logging

import httpx
from dashboard_config import (
    FLEET_NODES,
    HOSTNAME,
    MACHINE_ROLE,
    ORG,
    HttpTimeout,
    runner_limit,
)
from fastapi import APIRouter, Depends, Request, Response
from fleet_autoconfig import derive_pool_topology  # issue #942 — registry-driven pool topology
from fleet_events import (  # issue #863 — record runner/disk transitions
    FleetEventPoller,
    nodes_from_fleet_status,
)
from gh_utils import gh_api_admin
from identity import require_fleet_peer  # issue #922 — intra-fleet auth gate
from machine_registry import load_machine_registry
from proxy_utils import proxy_to_hub, should_mark_hub_circuit_degraded, should_proxy_fleet_to_hub
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


async def _fleet_control_local(action: str) -> dict:
    """Scale runners on this machine only."""
    data = await fetch_org_runners(gh_api_admin, ORG)
    runners = data.get("runners", [])
    results = []

    log.info("Local runner control on %s: %s", HOSTNAME, action)

    if action == "all-up":
        for i in range(1, runner_limit() + 1):
            if runner_svc_path(i).exists():
                code, _, _ = await run_runner_svc(i, "start")
                results.append({"runner": i, "action": "start", "success": code == 0})

    elif action == "all-down":
        for i in range(1, runner_limit() + 1):
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
        for i in range(1, runner_limit() + 1):
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


@router.get("/api/fleet/status", dependencies=[Depends(require_fleet_peer)])
async def get_fleet_status(request: Request, response: Response, exclude_pools: bool = False):
    """Get full system metrics state for all machines in the fleet network.

    Gated by require_fleet_peer (#922): when HUB_FLEET_TOKEN is set on this node,
    callers must present a valid principal or the fleet bearer token; otherwise
    (no token configured) fleet reads remain tailnet-public.
    """
    if should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    degraded_by_hub_circuit = should_mark_hub_circuit_degraded(request)

    responses = {}
    local_metrics = await get_system_metrics_snapshot()
    local_metrics["_role"] = "hub" if MACHINE_ROLE == "hub" else "node"

    from dashboard_config import PORT, RUNNER_ALIASES

    # Issue #942: derive split-pool topology from machine_registry.yml instead of
    # hardcoding one machine's port layout. Single-pool machines (the common
    # case) get local_pool_name=None and no peer pools, so no phantom offline
    # node is emitted.
    registry = load_machine_registry()
    local_pool_name, peer_pools = derive_pool_topology(
        registry,
        local_port=PORT,
        display_name=HOSTNAME,
        platform_node=None,
        runner_aliases=RUNNER_ALIASES,
    )

    responses[local_pool_name or HOSTNAME] = local_metrics

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
        for peer in peer_pools:
            peer_pool_name = peer["name"]
            peer_url = f"{peer['url']}/api/fleet/status?exclude_pools=true"
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(peer_url, timeout=HttpTimeout.PROXY_NODE_SYSTEM_S)
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
                log.warning("Failed to query peer pool %s at %s: %s", peer_pool_name, peer_url, e)
                reason = classify_node_offline(e)
                responses[peer_pool_name] = {
                    "status": "offline",
                    "error": reason["offline_detail"],
                    **reason,
                }

    _record_fleet_events(responses)
    if degraded_by_hub_circuit:
        responses["_degraded"] = True
        response.headers["X-Dashboard-Degraded"] = "hub-circuit-open"
    return responses


# /api/health is defined in backend/health.py and registered via _health_router.
