"""Proxy staff run details, events, and SSE streams across peer fleet nodes (SC-B7, Issue #1314).

Enables hub or peer nodes to transparently locate, inspect, cancel, and stream runs
that are executing on other machines across the fleet without returning 404.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
from staff import fleet as staff_fleet

log = logging.getLogger("dashboard.staff.remote_runs")

# Cache to avoid fanout on repeated queries for the same run_id: {run_id: (peer_name, peer_url)}
_RUN_PEER_CACHE: dict[str, tuple[str, str]] = {}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def find_remote_run(
    run_id: str,
    peers: dict[str, str] | None = None,
) -> tuple[str, str] | None:
    """Locate which peer node owns the given run_id.

    Returns (peer_name, peer_url) or None if not found on any peer.
    """
    if run_id in _RUN_PEER_CACHE:
        cached_peer, cached_url = _RUN_PEER_CACHE[run_id]
        return cached_peer, cached_url

    nodes = peers if peers is not None else staff_fleet.peer_nodes()
    if not nodes:
        return None

    headers = staff_fleet.fleet_headers()
    for name, url in nodes.items():
        try:
            data = await staff_fleet.get_json(f"{url}/api/staff/runs/{run_id}?events=0", headers)
            if data and data.get("run", {}).get("id") == run_id:
                _RUN_PEER_CACHE[run_id] = (name, url)
                return name, url
        except Exception:  # noqa: BLE001
            continue

    return None


async def proxy_remote_run(
    run_id: str,
    events: int,
    peer_name: str,
    peer_url: str,
    caller_id: str = "",
) -> dict[str, Any]:
    """Fetch run details and event log from the peer node that owns it."""
    headers = staff_fleet.fleet_headers()
    if caller_id:
        headers["X-Staff-On-Behalf-Of"] = staff_fleet.sign_on_behalf_of(caller_id, surface="api")

    try:
        data = await staff_fleet.get_json(
            f"{peer_url}/api/staff/runs/{run_id}?events={events}",
            headers,
        )
        return data
    except Exception as exc:  # noqa: BLE001
        log.warning("staff.remote_runs: peer %s unreachable for run %s: %s", peer_name, run_id, exc)
        return {
            "run": {
                "id": run_id,
                "machine": peer_name,
                "status": "node_unreachable",
                "error": f"Node {peer_name} unreachable since {_now_iso()}: {exc}",
                "failure_class": "node_offline",
            },
            "events": [],
            "attempts": [],
            "unreachable_since": _now_iso(),
        }


async def stream_remote_run(
    run_id: str,
    after: int,
    peer_name: str,
    peer_url: str,
    caller_id: str = "",
) -> AsyncIterator[str]:
    """Proxy an SSE event stream from a peer node to the caller."""
    headers = staff_fleet.fleet_headers()
    headers["Accept"] = "text/event-stream"
    if caller_id:
        headers["X-Staff-On-Behalf-Of"] = staff_fleet.sign_on_behalf_of(caller_id, surface="api")

    url = f"{peer_url}/api/staff/runs/{run_id}/stream?after={after}"
    timeout = httpx.Timeout(connect=5.0, read=None, write=5.0, pool=None)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code >= 400:
                    err_payload = json.dumps({"run_id": run_id, "machine": peer_name, "status_code": resp.status_code})
                    yield f"event: error\ndata: {err_payload}\n\n"
                    return
                async for line in resp.aiter_lines():
                    yield f"{line}\n"
    except Exception as exc:  # noqa: BLE001
        log.warning("staff.remote_runs: remote SSE stream dropped for %s on %s: %s", run_id, peer_name, exc)
        unreach_payload = json.dumps(
            {
                "run_id": run_id,
                "machine": peer_name,
                "error": f"node {peer_name} unreachable since {_now_iso()}",
                "unreachable_since": _now_iso(),
            }
        )
        yield f"event: node_unreachable\ndata: {unreach_payload}\n\n"


async def proxy_remote_cancel(
    run_id: str,
    peer_name: str,
    peer_url: str,
    caller_id: str = "",
) -> dict[str, Any]:
    """Forward a cancel request for a remote run to the owning peer."""
    headers = staff_fleet.fleet_headers()
    if caller_id:
        headers["X-Staff-On-Behalf-Of"] = staff_fleet.sign_on_behalf_of(caller_id, surface="api")

    status, data = await staff_fleet.post_json(
        f"{peer_url}/api/staff/runs/{run_id}/cancel",
        {},
        headers,
    )
    return data
