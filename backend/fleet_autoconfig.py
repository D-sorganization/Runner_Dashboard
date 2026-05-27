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
