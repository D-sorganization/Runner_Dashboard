"""Fleet management routes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any

import httpx
from dashboard_config import (
    DEPLOYMENT_FILE,
    FLEET_NODES,
    HOSTNAME,
    MACHINE_ROLE,
    ORG,
    VERSION,
    HttpTimeout,
    runner_limit,
)
from fastapi import APIRouter, Depends, Request, Response
from fleet_autoconfig import (
    _local_dashboard_url_candidates,
    derive_fleet_nodes_from_registry,
    derive_pool_topology,
)  # issue #942, #1169 — registry-driven pool topology & federation
from fleet_events import (  # issue #863 — record runner/disk transitions
    FleetEventPoller,
    nodes_from_fleet_status,
)
from fleet_status_cache import fleet_status_cache_key, serve_fleet_status
from gh_utils import gh_api_admin
from identity import require_fleet_peer  # issue #922 — intra-fleet auth gate
from machine_registry import load_machine_registry
from node_auto_deploy_status import read_auto_deploy_fleet_status
from proxy_utils import (
    proxy_to_hub,
    should_mark_hub_circuit_degraded,
    should_proxy_fleet_to_hub,
)
from routers.runners import run_runner_svc, runner_num_from_id, runner_svc_path
from runner_inventory import fetch_org_runners
from system_utils import (
    classify_node_offline,
    get_deployment_info,
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


def _node_probe_timeout() -> httpx.Timeout:
    """Bounded HTTP timeout for cross-node fleet probes (#2110).

    Fleet status fans out concurrently; each peer gets a ~2 s read budget and
    a 1 s connect cap so one offline host cannot dominate hub latency.
    """
    return httpx.Timeout(
        HttpTimeout.FLEET_NODE_PROBE_READ_S,
        connect=HttpTimeout.FLEET_NODE_PROBE_CONNECT_S,
    )


async def _fetch_peer_pool(peer: dict, *, local_dashboard_urls: frozenset[str]) -> dict[str, dict]:
    """Fetch one peer pool's fleet status, classifying it offline on failure.

    Returns a ``{node_name: metrics}`` mapping to merge into the aggregate
    response. Independent per peer (no shared state) so callers can ``gather``
    them concurrently; an unreachable peer resolves to a single offline entry
    rather than raising.
    """
    peer_pool_name = peer["name"]
    peer_base = str(peer["url"]).strip().rstrip("/").lower()
    # Co-located pools on the same host must not recurse into our own
    # ``/api/fleet/status`` over loopback: with a single uvicorn worker that
    # deadlocks until the client times out (~30s).
    if peer_base in local_dashboard_urls:
        peer_url = f"{peer['url'].rstrip('/')}/api/system"
        single_key = True
    else:
        peer_url = f"{peer['url'].rstrip('/')}/api/fleet/status?exclude_pools=true"
        single_key = False
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(peer_url, timeout=_node_probe_timeout())
            if resp.status_code == 200:
                payload = resp.json()
                if single_key and isinstance(payload, dict):
                    return {peer_pool_name: payload}
                return (
                    {k: v for k, v in payload.items() if not str(k).startswith("_")}
                    if isinstance(payload, dict)
                    else {}
                )
            reason = classify_node_offline(status_code=resp.status_code)
            return {
                peer_pool_name: {
                    "status": "offline",
                    "error": reason["offline_detail"],
                    **reason,
                }
            }
    except Exception as e:  # noqa: BLE001 — any failure means the peer is offline
        log.warning("Failed to query peer pool %s at %s: %s", peer_pool_name, peer_url, e)
        reason = classify_node_offline(e)
        return {
            peer_pool_name: {
                "status": "offline",
                "error": reason["offline_detail"],
                **reason,
            }
        }


async def _collect_fleet_status(*, exclude_pools: bool) -> dict[str, Any]:
    """Build the fleet status aggregate (uncached)."""
    import os

    from dashboard_config import PORT, RUNNER_ALIASES

    responses: dict[str, Any] = {}

    registry = load_machine_registry()
    local_pool_name, peer_pools = derive_pool_topology(
        registry,
        local_port=PORT,
        display_name=HOSTNAME,
        platform_node="",
        runner_aliases=RUNNER_ALIASES,
    )
    local_dashboard_urls = frozenset(
        _local_dashboard_url_candidates(
            registry,
            display_name=HOSTNAME,
            platform_node="",
            runner_aliases=RUNNER_ALIASES,
            local_port=PORT,
        )
    )

    nodes_to_query = dict(FLEET_NODES)
    if not nodes_to_query and os.environ.get("AUTODERIVE_FLEET_NODES", "1").lower() not in {"0", "false", "no", ""}:
        nodes_to_query = derive_fleet_nodes_from_registry(
            registry,
            display_name=HOSTNAME,
            platform_node="",
            runner_aliases=RUNNER_ALIASES,
        )

    async def fetch_node(name: str, url: str) -> tuple[str, dict[str, Any]]:
        try:
            async with httpx.AsyncClient() as client:
                target = f"{url}/api/system"
                resp = await client.get(target, timeout=_node_probe_timeout())
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

    tasks: list[Awaitable[Any]] = [get_system_metrics_snapshot()]
    node_items = list(nodes_to_query.items()) if not exclude_pools else []
    tasks.extend(fetch_node(name, url) for name, url in node_items)
    peer_list = peer_pools if not exclude_pools else []
    tasks.extend(_fetch_peer_pool(peer, local_dashboard_urls=local_dashboard_urls) for peer in peer_list)

    gathered = await asyncio.gather(*tasks)
    local_metrics = gathered[0]
    from runner_control_health import get_runner_control_health  # noqa: PLC0415

    local_metrics["runner_control"] = get_runner_control_health()
    local_metrics["deployment"] = get_deployment_info(VERSION, DEPLOYMENT_FILE)
    local_metrics["auto_deploy"] = read_auto_deploy_fleet_status()
    local_metrics["_role"] = "hub" if MACHINE_ROLE == "hub" else "node"
    local_res_reason = resource_offline_reason(local_metrics)
    if local_res_reason:
        local_metrics.update(local_res_reason)
    responses[local_pool_name or HOSTNAME] = local_metrics

    node_results = gathered[1 : 1 + len(node_items)]
    for item in node_results:
        name, data = item
        responses[name] = data

    peer_results = gathered[1 + len(node_items) :]
    for updates in peer_results:
        responses.update(updates)

    _record_fleet_events(responses)
    return responses


@router.get("/api/fleet/status", dependencies=[Depends(require_fleet_peer)])
async def get_fleet_status(request: Request, response: Response, exclude_pools: bool = False):
    """Get full system metrics state for all machines in the fleet network.

    Gated by require_fleet_peer (#922): when HUB_FLEET_TOKEN is set on this node,
    callers must present a valid principal or the fleet bearer token; otherwise
    (no token configured) fleet reads remain tailnet-public.
    """
    if not exclude_pools and should_proxy_fleet_to_hub(request):
        return await proxy_to_hub(request)
    degraded_by_hub_circuit = should_mark_hub_circuit_degraded(request)

    cache_key = fleet_status_cache_key(exclude_pools=exclude_pools)

    async def collect() -> dict[str, Any]:
        return await _collect_fleet_status(exclude_pools=exclude_pools)

    responses = await serve_fleet_status(cache_key, collect)
    if degraded_by_hub_circuit:
        responses = {**responses, "_degraded": True}
        response.headers["X-Dashboard-Degraded"] = "hub-circuit-open"
    try:
        from email_intake.health import email_intake_status

        responses["_email_intake"] = email_intake_status()
    except Exception as exc:  # pragma: no cover - observability only
        log.debug("email intake status skipped: %s", exc)
    return responses


# /api/health is defined in backend/health.py and registered via _health_router.
