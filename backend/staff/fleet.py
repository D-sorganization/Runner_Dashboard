"""Fleet-wide view of staff runs: peer discovery, board aggregation, placement, forwarding.

Issues #1195 (hub fan-out board + summary) and #1197 (machine targeting).

The hub asks every peer node for its *local* ``/api/staff/board`` and merges
the answers; a node that cannot be reached is reported as ``offline`` rather
than failing the whole board (orthogonality of failure domains, same rule as
``/api/fleet/status``). Dispatches aimed at another machine are forwarded to
that node's ``/api/staff/{role}/run`` with the fleet bearer token.

HTTP calls go through two small injectable functions (``get_json`` /
``post_json``) so tests never touch the network.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from dashboard_config import FLEET_NODES, HOSTNAME, RUNNER_ALIASES
from fleet_autoconfig import derive_fleet_nodes_from_registry
from machine_registry import load_machine_registry
from staff import liveness as staff_liveness
from staff.adapters import available_providers
from staff.rm_sync import source_status
from staff.store import ACTIVE_STATUSES
from staff.usage import today_iso

log = logging.getLogger("dashboard.staff.fleet")

PEER_TIMEOUT_SECONDS = float(os.environ.get("STAFF_PEER_TIMEOUT_SECONDS", "6"))
LOCAL_MACHINE_ALIASES = frozenset({"local", "", "self", "here"})

GetJson = Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]]
PostJson = Callable[[str, dict[str, Any], dict[str, str]], Awaitable[tuple[int, dict[str, Any]]]]


async def _get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=PEER_TIMEOUT_SECONDS) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"value": data}


async def _post_json(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
    async with httpx.AsyncClient(timeout=PEER_TIMEOUT_SECONDS * 5) as client:
        resp = await client.post(url, json=body, headers=headers)
        try:
            data = resp.json()
        except ValueError:
            data = {"detail": resp.text[:500]}
        return resp.status_code, data if isinstance(data, dict) else {"value": data}


get_json: GetJson = _get_json
post_json: PostJson = _post_json


def fleet_headers() -> dict[str, str]:
    """Headers for hub → node calls: fleet bearer token (if configured) + CSRF sentinel."""
    headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
    token = os.environ.get("HUB_FLEET_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def peer_nodes() -> dict[str, str]:
    """Name → base URL of every other dashboard node (env first, registry second)."""
    nodes = dict(FLEET_NODES)
    if not nodes and os.environ.get("AUTODERIVE_FLEET_NODES", "1").lower() not in {
        "0",
        "false",
        "no",
        "",
    }:
        try:
            nodes = derive_fleet_nodes_from_registry(
                load_machine_registry(),
                display_name=HOSTNAME,
                platform_node="",
                runner_aliases=RUNNER_ALIASES,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("staff.fleet: registry-derived peers unavailable: %s", exc)
            nodes = {}
    return {name: url.rstrip("/") for name, url in nodes.items()}


def resolve_machine(name: str, local_machine: str, peers: dict[str, str]) -> str | None:
    """Map a requested machine name to a peer key, ``"local"`` or ``None`` (unknown).

    Matching is case-insensitive and tolerant of the local host's own name.
    """
    wanted = name.strip().lower()
    if wanted in LOCAL_MACHINE_ALIASES or wanted == local_machine.lower():
        return "local"
    for key in peers:
        if key.lower() == wanted:
            return key
    return None


async def fetch_peer_board(name: str, url: str) -> tuple[str, dict[str, Any]]:
    try:
        data = await get_json(f"{url}/api/staff/board?local=1", fleet_headers())
        data["status"] = "online"
        return name, data
    except Exception as exc:  # noqa: BLE001
        log.info("staff.fleet: peer %s board unavailable: %s", name, exc)
        return name, {"machine": name, "status": "offline", "error": str(exc)[:200]}


async def aggregate_board(local_board: dict[str, Any], peers: dict[str, str] | None = None) -> dict[str, Any]:
    """Merge this node's board with every peer's board into one fleet view.

    Post: ``machines`` has one entry per known node (online or offline);
    ``running``/``queued`` are the concatenation across online nodes; spend
    totals are summed per provider; ``liveness_alerts`` are the late/dead
    scheduled roles across online nodes, each tagged with its machine (#1209).
    """
    peers = peer_nodes() if peers is None else peers
    local_name = str(local_board.get("machine") or HOSTNAME)
    machines: dict[str, dict[str, Any]] = {local_name: {**local_board, "status": "online"}}
    if peers:
        results = await asyncio.gather(*[fetch_peer_board(n, u) for n, u in peers.items()])
        for name, board in results:
            machines[name] = board
    running: list[dict[str, Any]] = []
    queued: list[dict[str, Any]] = []
    spend: dict[str, float] = {}
    providers: dict[str, dict[str, bool]] = {}
    liveness_alerts: list[dict[str, Any]] = []
    for name, board in machines.items():
        if board.get("status") != "online":
            continue
        running.extend(board.get("running", []))
        queued.extend(board.get("queued", []))
        liveness_alerts.extend(staff_liveness.alerts(board.get("liveness") or [], name))
        for provider, usd in (board.get("spend_today_usd") or {}).items():
            if provider != "total":
                spend[provider] = round(spend.get(provider, 0.0) + float(usd), 6)
        providers[name] = dict(board.get("providers") or {})
    spend["total"] = round(sum(v for k, v in spend.items() if k != "total"), 6)
    return {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "hub": local_name,
        "machine": local_name,
        "machines": machines,
        "online": sorted(n for n, b in machines.items() if b.get("status") == "online"),
        "offline": sorted(n for n, b in machines.items() if b.get("status") != "online"),
        "running": running,
        "queued": queued,
        "recent": [],
        "spend_today_usd": spend,
        "providers": providers,
        "liveness_alerts": liveness_alerts,
    }


def choose_machine(board: dict[str, Any], provider: str | None, local_name: str) -> str:
    """Pick the online machine with the fewest active runs that has ``provider`` installed.

    Ties go to the local node. Falls back to local when nothing qualifies, so a
    degraded fleet still runs work somewhere.
    """
    best: tuple[int, int, str] | None = None
    for name, node in board.get("machines", {}).items():
        if node.get("status") != "online":
            continue
        installed = node.get("providers") or {}
        if provider and not installed.get(provider, False):
            continue
        load = len(node.get("running", [])) + len(node.get("queued", []))
        rank = (load, 0 if name == local_name else 1, name)
        if best is None or rank < best:
            best = rank
    return best[2] if best else local_name


def sign_on_behalf_of(
    principal: str,
    surface: str = "api",
    thread_id: str = "",
    request_id: str = "",
    secret: str | None = None,
) -> str:
    """Create a signed X-Staff-On-Behalf-Of header value (issue #1311).

    Format: base64url(json_payload).hmac_signature_hex
    """
    key = secret if secret is not None else os.environ.get("HUB_FLEET_TOKEN") or os.environ.get("SESSION_SECRET") or ""
    payload = {
        "principal": principal,
        "surface": surface,
        "thread_id": thread_id,
        "request_id": request_id,
        "iat": int(time.time()),
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_json).decode("ascii")
    sig = hmac.new(key.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_on_behalf_of(
    header: str,
    secret: str | None = None,
    ttl_seconds: int = 300,
) -> dict[str, Any] | None:
    """Verify an X-Staff-On-Behalf-Of header value (issue #1311).

    Returns payload dict if valid, unexpired, and non-empty principal, else None.
    """
    key = secret if secret is not None else os.environ.get("HUB_FLEET_TOKEN") or os.environ.get("SESSION_SECRET") or ""
    if not header or "." not in header:
        return None
    try:
        payload_b64, sig = header.split(".", 1)
        expected_sig = hmac.new(key.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload_json = base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8")
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            return None
        principal = payload.get("principal")
        if not principal or not isinstance(principal, str) or not principal.strip():
            return None
        iat = payload.get("iat")
        if not isinstance(iat, (int, float)):
            return None
        now = time.time()
        if now - iat > ttl_seconds or iat - now > 60:
            return None
        return payload
    except Exception as exc:  # noqa: BLE001
        log.debug("verify_on_behalf_of failed: %s", exc)
        return None


def is_fleet_peer(principal: Any) -> bool:
    """True if principal is authenticated as a fleet peer (issue #1311)."""
    roles = getattr(principal, "roles", []) or []
    pid = getattr(principal, "id", "")
    return pid in ("fleet-peer", "test-peer") or "fleet-peer" in roles


def caller_identity(principal: Any) -> str:
    """Format principal identity for run records (issue #1311)."""
    pid = getattr(principal, "id", str(principal))
    if pid in ("fleet-peer", "__loopback__", "loopback-dev", "test-orchestrator", "test-peer"):
        return pid
    if pid.startswith("user:") or pid.startswith("principal:"):
        return pid
    return f"principal:{pid}"


def extract_on_behalf_of(header: str | None, is_peer: bool) -> dict[str, Any] | None:
    """Validate and unpack on-behalf-of header if caller is a peer (issue #1311)."""
    if not header:
        return None
    if not is_peer:
        log.warning("staff: ignored X-Staff-On-Behalf-Of header from non-peer")
        return None
    verified = verify_on_behalf_of(header)
    if verified is None:
        log.warning("staff: invalid X-Staff-On-Behalf-Of header from peer")
        return None
    return verified


async def forward_run(
    url: str,
    role: str,
    body: dict[str, Any],
    on_behalf_of: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """POST a dispatch to a peer node; returns (status_code, json)."""
    payload = {**body, "machine": "local"}
    headers = fleet_headers()
    if on_behalf_of:
        headers["X-Staff-On-Behalf-Of"] = on_behalf_of
    return await post_json(f"{url}/api/staff/{role}/run", payload, headers)


def local_board(runner: Any) -> dict[str, Any]:
    store = runner.store
    active = store.active_runs()
    recent = store.list_runs(limit=20)
    now = datetime.now(UTC)
    liveness = staff_liveness.compute_liveness(runner.roles(), store, staff_liveness.load_scheduler_state(), now)
    staff_liveness.notify_dead(liveness, runner.machine)
    return {
        "machine": runner.machine,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "running": [r.to_dict() for r in active if r.status == "running"],
        "queued": [r.to_dict() for r in active if r.status in ("queued", "preparing")],
        "recent": [r.to_dict() for r in recent if r.status not in ACTIVE_STATUSES],
        "spend_today_usd": store.spend_since(today_iso()),
        "providers": available_providers(),
        "liveness": liveness,
        "rm_source": getattr(sys.modules.get("routers.staff"), "source_status", source_status)(),
    }


def holds_snapshot() -> list[dict[str, Any]]:
    """Active holds when the scheduler module (#1196) is present; empty otherwise."""
    try:
        from staff import holds as staff_holds  # noqa: PLC0415
    except ImportError:
        return []
    loader = getattr(staff_holds, "active_holds", None)
    if loader is None:
        return []
    try:
        return [dict(h) for h in loader()]
    except Exception as exc:  # noqa: BLE001
        log.warning("staff: holds unavailable: %s", exc)
        return []
