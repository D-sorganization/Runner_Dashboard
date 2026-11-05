"""Host readyz gate for agent_bus stale-session claim reaping (#3823).

A session goes stale when its heartbeats stop, which is also what happens
when the node it registered from is unhealthy. Reaping then would release
GitHub claims that a live agent still works under, so a stale session is
only reaped once its registering node's ``/readyz`` reports ok.

Nodes this dashboard cannot resolve (not local, not a known peer) keep the
pre-#3823 behaviour and are reaped: there is no health signal to wait for,
and deferring forever would leak their claims.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping

import httpx
from dashboard_config import HOSTNAME
from staff.fleet import PEER_TIMEOUT_SECONDS, fleet_headers, peer_nodes, resolve_machine

log = logging.getLogger("dashboard.agent_bus.reaper_health")

ReapGate = Callable[[str], bool]
_LOCAL = "local"


def local_readyz_ok() -> bool:
    """True when this node's default readiness probes aggregate to ok.

    Fails closed: if readiness cannot be evaluated, local reaping waits.
    Must run off the event loop (the reaper calls it via ``asyncio.to_thread``).
    """
    from readiness import aggregate, get_default_probes  # noqa: PLC0415 — heavy import, reaper-only

    try:
        status, _body = asyncio.run(aggregate(get_default_probes()))
    except Exception as exc:  # noqa: BLE001 — fail closed
        log.warning("agent_bus reaper: local readyz unavailable: %s", exc)
        return False
    return status == 200


def peer_readyz_ok(url: str) -> bool:
    """True when the peer at *url* answers ``/readyz`` with HTTP 200 and status ok. Fails closed."""
    try:
        resp = httpx.get(f"{url.rstrip('/')}/readyz", headers=fleet_headers(), timeout=PEER_TIMEOUT_SECONDS)
        data = resp.json() if resp.status_code == 200 else None
    except Exception as exc:  # noqa: BLE001 — fail closed
        log.info("agent_bus reaper: peer readyz unavailable at %s: %s", url, exc)
        return False
    return isinstance(data, dict) and str(data.get("status") or "").lower() == "ok"


def node_reap_gate(
    *,
    local_machine: str | None = None,
    peers: Mapping[str, str] | None = None,
    local_ready: Callable[[], bool] = local_readyz_ok,
    peer_ready: Callable[[str], bool] = peer_readyz_ok,
) -> ReapGate:
    """Build a one-pass gate: ``gate(node)`` is True when a stale session on *node* may be reaped.

    Each resolved node's readiness is checked at most once per gate, so build
    a fresh gate for every reap pass.
    """
    local_name = HOSTNAME if local_machine is None else local_machine
    peer_map = dict(peer_nodes() if peers is None else peers)
    verdicts: dict[str, bool] = {}

    def allows(node: str) -> bool:
        if not isinstance(node, str):
            raise TypeError(f"node must be str, got {type(node).__name__}")
        target = resolve_machine(node, local_name, peer_map)
        if target is None:
            return True
        if target not in verdicts:
            verdicts[target] = local_ready() if target == _LOCAL else peer_ready(peer_map[target])
        return verdicts[target]

    return allows
