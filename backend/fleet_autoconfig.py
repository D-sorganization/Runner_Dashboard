"""Helpers for deriving fleet peers from ``machine_registry.yml``.

The dashboard can run as either a physical machine entry (for example
``DeskComputer``) or as one pool under a split host (for example
``ControlTower-NVMe`` under the physical ``ControlTower`` machine).  Keeping
this logic separate from ``server.py`` makes the self-exclusion rules testable.
"""

from __future__ import annotations

import platform
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit


def normalize_fleet_label(value: object) -> str:
    """Normalize a machine, pool, or alias label for identity comparisons."""
    return str(value or "").strip().lower()


def current_identity_names(
    *,
    display_name: str | None = None,
    platform_node: str | None = None,
    runner_aliases: str | Iterable[str] | None = None,
) -> set[str]:
    """Return all names that should be treated as the local dashboard."""
    identities = {
        normalize_fleet_label(display_name),
        normalize_fleet_label(platform_node if platform_node is not None else platform.node()),
    }
    if isinstance(runner_aliases, str):
        identities.update(normalize_fleet_label(item) for item in runner_aliases.split(","))
    elif runner_aliases is not None:
        identities.update(normalize_fleet_label(item) for item in runner_aliases)
    return {item for item in identities if item}


def registry_machine_identity_names(machine: Mapping[str, Any]) -> set[str]:
    """Return every identity name owned by a registry machine and its pools."""
    names = {normalize_fleet_label(machine.get("name"))}
    names.update(normalize_fleet_label(alias) for alias in machine.get("aliases", []) or [])
    for pool in machine.get("runner_pools", []) or []:
        if not isinstance(pool, Mapping):
            continue
        names.add(normalize_fleet_label(pool.get("name")))
        names.update(normalize_fleet_label(alias) for alias in pool.get("aliases", []) or [])
    return {name for name in names if name}


def registry_machine_matches_current_dashboard(machine: Mapping[str, Any], identities: set[str]) -> bool:
    """Return true when the current dashboard is this machine or one of its pools."""
    return bool(registry_machine_identity_names(machine) & identities)


def registry_machine_url(machine: Mapping[str, Any]) -> str:
    """Return the preferred dashboard URL for a registry machine."""
    candidate = str(machine.get("dashboard_url") or "").strip()
    if candidate:
        return candidate
    for node in machine.get("tailscale_nodes", []) or []:
        if not isinstance(node, Mapping):
            continue
        ip = str(node.get("ip", "")).strip()
        if ip:
            return f"http://{ip}:8321"
    return ""


def _pool_url_port(url: str) -> int | None:
    """Return the explicit port in a pool ``dashboard_url``, or None."""
    if not url:
        return None
    try:
        return urlsplit(url).port
    except ValueError:
        return None


def derive_pool_topology(
    registry: Mapping[str, Any],
    *,
    local_port: int,
    display_name: str | None = None,
    platform_node: str | None = None,
    runner_aliases: str | Iterable[str] | None = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Derive this dashboard's pool identity and its sibling pools to probe.

    A *pool* is a ``runner_pools`` entry under the physical machine this
    dashboard runs on (for example ``ControlTower-NVMe`` / ``ControlTower-SSD``
    under ``ControlTower``).  Single-pool machines (the common case) own no
    sibling pools, so nothing is probed and no phantom peer node appears.

    Returns ``(local_pool_name, peer_pools)`` where ``peer_pools`` is a list of
    ``{"name", "url", "port"}`` dicts for every *other* pool under the same
    physical machine.  ``local_pool_name`` is ``None`` when the host is not a
    split-pool machine.

    The local pool is matched first by identity name (display name / aliases),
    then — when identity is ambiguous — by the port in the pool's
    ``dashboard_url`` matching ``local_port``.  This replaces the old
    ``if PORT == 8322`` hardcoding so a third pool or a renamed host needs only
    a registry edit, never a code change (issue #942).
    """
    identities = current_identity_names(
        display_name=display_name,
        platform_node=platform_node,
        runner_aliases=runner_aliases,
    )
    for machine in registry.get("machines", []) or []:
        if not isinstance(machine, Mapping):
            continue
        pools = [p for p in (machine.get("runner_pools") or []) if isinstance(p, Mapping)]
        if len(pools) < 2:
            continue
        if not registry_machine_matches_current_dashboard(machine, identities):
            continue

        local_pool: Mapping[str, Any] | None = None
        for pool in pools:
            pool_names = {normalize_fleet_label(pool.get("name"))}
            pool_names.update(normalize_fleet_label(a) for a in pool.get("aliases", []) or [])
            if pool_names & identities:
                local_pool = pool
                break
        if local_pool is None:
            for pool in pools:
                if _pool_url_port(str(pool.get("dashboard_url") or "")) == local_port:
                    local_pool = pool
                    break
        if local_pool is None:
            continue

        local_name = str(local_pool.get("name") or "").strip() or None
        peers: list[dict[str, Any]] = []
        for pool in pools:
            if pool is local_pool:
                continue
            name = str(pool.get("name") or "").strip()
            url = str(pool.get("dashboard_url") or "").strip().rstrip("/")
            if name and url:
                peers.append({"name": name, "url": url, "port": _pool_url_port(url)})
        return local_name, peers
    return None, []


def assert_no_maxwell_port_collision(
    registry: Mapping[str, Any],
    *,
    maxwell_port: int,
    local_port: int,
) -> None:
    """Fail fast when Maxwell's port collides with a registry dashboard port.

    Issue #942: the registry's second pool reserved :8322, which historically
    matched Maxwell's default port — so a default deploy probed a second
    dashboard and misreported it as Maxwell. We refuse to start when
    ``MAXWELL_PORT`` equals a *peer* dashboard port (any registry
    ``dashboard_url`` port other than this dashboard's own ``local_port``),
    surfacing the misconfiguration loudly instead of silently mis-probing.

    Raises ``RuntimeError`` on collision; returns ``None`` otherwise.
    """
    peer_ports = registry_dashboard_url_ports(registry) - {local_port}
    if maxwell_port in peer_ports:
        raise RuntimeError(
            f"MAXWELL_PORT={maxwell_port} collides with a fleet dashboard port in "
            f"machine_registry.yml (ports {sorted(peer_ports)}). Set MAXWELL_PORT / "
            f"MAXWELL_URL to a distinct port, or fix the registry dashboard_url."
        )


def registry_dashboard_url_ports(registry: Mapping[str, Any]) -> set[int]:
    """Return every explicit port used by a machine or pool ``dashboard_url``."""
    ports: set[int] = set()
    for machine in registry.get("machines", []) or []:
        if not isinstance(machine, Mapping):
            continue
        port = _pool_url_port(str(machine.get("dashboard_url") or ""))
        if port is not None:
            ports.add(port)
        for pool in machine.get("runner_pools", []) or []:
            if not isinstance(pool, Mapping):
                continue
            port = _pool_url_port(str(pool.get("dashboard_url") or ""))
            if port is not None:
                ports.add(port)
    return ports


def derive_fleet_nodes_from_registry(
    registry: Mapping[str, Any],
    *,
    display_name: str | None = None,
    platform_node: str | None = None,
    runner_aliases: str | Iterable[str] | None = None,
) -> dict[str, str]:
    """Derive remote fleet peers from the machine registry.

    If this dashboard represents a runner pool under a physical host, skip the
    whole physical-host entry.  The local dashboard already supplies local
    metrics without an HTTP round-trip, and probing the parent host URL creates
    false offline states or mirrored-network port conflicts.
    """
    identities = current_identity_names(
        display_name=display_name,
        platform_node=platform_node,
        runner_aliases=runner_aliases,
    )
    nodes: dict[str, str] = {}
    for machine in registry.get("machines", []) or []:
        if not isinstance(machine, Mapping):
            continue
        name = str(machine.get("name", "")).strip()
        if not name or registry_machine_matches_current_dashboard(machine, identities):
            continue
        url = registry_machine_url(machine)
        if url:
            nodes[name] = url
    return nodes
