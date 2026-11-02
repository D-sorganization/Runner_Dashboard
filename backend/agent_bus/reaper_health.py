"""Health gate for agent_bus claim reaping on remote fleet nodes (#3823)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from dashboard_config import HOSTNAME

log = logging.getLogger("dashboard.agent_bus.reaper_health")

PeerReadyCheck = Callable[[str, str], bool]
LocalReadyCheck = Callable[[], bool]

_peer_ready_check: PeerReadyCheck | None = None
_local_ready_check: LocalReadyCheck | None = None


def _normalize_node(name: str) -> str:
    return name.strip().casefold()


def is_local_node(node: str, *, local_machine: str | None = None) -> bool:
    local = _normalize_node(local_machine or HOSTNAME)
    wanted = _normalize_node(node)
    return wanted in {"", "local", "self", "here", local}


def local_readyz_ok() -> bool:
    """True when this process's default readiness probes all report ok."""
    if _local_ready_check is not None:
        return _local_ready_check()
    try:
        import asyncio
        from readiness import aggregate, get_default_probes

        status, _body = asyncio.run(aggregate(get_default_probes()))
        return status == 200
    except Exception as exc:  # noqa: BLE001 — fail closed: do not reap when readiness cannot run
        log.warning("agent_bus reaper: local readyz check failed: %s", exc)
        return False


def peer_readyz_ok(node: str, peer_url: str) -> bool:
    if _peer_ready_check is not None:
        return _peer_ready_check(node, peer_url)
    try:
        import httpx

        from staff.fleet import PEER_TIMEOUT_SECONDS, fleet_headers

        resp = httpx.get(f"{peer_url.rstrip('/')}/readyz", headers=fleet_headers(), timeout=PEER_TIMEOUT_SECONDS)
        if resp.status_code != 200:
            return False
        data = resp.json()
        return isinstance(data, dict) and str(data.get("status") or "").lower() == "ok"
    except Exception as exc:  # noqa: BLE001
        log.info("agent_bus reaper: peer %s readyz unavailable: %s", node, exc)
        return False


def node_ready_for_claim_reap(node: str, *, peers: dict[str, str] | None = None) -> bool:
    """Cross-host sessions are not reaped until the registering node's readyz is ok."""
    if is_local_node(node):
        return local_readyz_ok()
    if peers is None:
        from staff.fleet import peer_nodes

        peers = peer_nodes()
    wanted = _normalize_node(node)
    for name, url in peers.items():
        if _normalize_node(name) == wanted:
            return peer_readyz_ok(name, url)
    log.info("agent_bus reaper: unknown node %s for health gate; deferring reap", node)
    return False


def set_ready_checks(*, local: LocalReadyCheck | None = None, peer: PeerReadyCheck | None = None) -> None:
    """Test seam for injectable readiness results."""
    global _local_ready_check, _peer_ready_check  # noqa: PLW0603
    _local_ready_check = local
    _peer_ready_check = peer
