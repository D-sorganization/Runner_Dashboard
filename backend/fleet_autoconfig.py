"""Helpers for deriving fleet peers from ``machine_registry.yml``.

The dashboard can run as either a physical machine entry (for example
``DeskComputer``) or as one pool under a split host (for example
``ControlTower-NVMe`` under the physical ``ControlTower`` machine).  Keeping
this logic separate from ``server.py`` makes the self-exclusion rules testable.
"""

from __future__ import annotations

import logging
import platform
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger("dashboard.fleet_autoconfig")


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
    for pool in machine.get("runner_pools", []) or []:
        if not isinstance(pool, Mapping) or pool.get("retired"):
            continue
        pool_url = str(pool.get("dashboard_url") or "").strip()
        if pool_url:
            return pool_url
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

    A *pool* is a non-retired ``runner_pools`` entry under the physical machine
    this dashboard runs on. Single-pool or retired-pool machines get
    ``local_pool_name=None`` (or the single active pool name) and no peer pools,
    so no phantom peer node is emitted.
    """
    identities = current_identity_names(
        display_name=display_name,
        platform_node=platform_node,
        runner_aliases=runner_aliases,
    )
    for machine in registry.get("machines", []) or []:
        if not isinstance(machine, Mapping):
            continue
        pools = [p for p in (machine.get("runner_pools") or []) if isinstance(p, Mapping) and not p.get("retired")]
        if not pools:
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
        if local_pool is None and len(pools) == 1:
            local_pool = pools[0]
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
    """Return every explicit port used by an active machine or pool ``dashboard_url``."""
    ports: set[int] = set()
    for machine in registry.get("machines", []) or []:
        if not isinstance(machine, Mapping) or machine.get("retired"):
            continue
        port = _pool_url_port(str(machine.get("dashboard_url") or ""))
        if port is not None:
            ports.add(port)
        for pool in machine.get("runner_pools", []) or []:
            if not isinstance(pool, Mapping) or pool.get("retired"):
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
        if machine.get("retired"):
            continue
        name = str(machine.get("name", "")).strip()
        if not name or registry_machine_matches_current_dashboard(machine, identities):
            continue
        url = registry_machine_url(machine)
        if url:
            nodes[name] = url
    return nodes


@dataclass(frozen=True)
class FleetIdentity:
    """Resolved hub-spoke identity for this dashboard process."""

    machine_role: str
    machine_role_source: str
    hub_url: str | None
    hub_url_source: str
    hub_machine_names: tuple[str, ...]


def _active_machines(registry: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    machines = registry.get("machines", []) or []
    return [m for m in machines if isinstance(m, Mapping) and not m.get("retired")]


def hub_machine_names(registry: Mapping[str, Any]) -> list[str]:
    """Return canonical names of non-retired machines whose ``role`` is ``hub``."""
    names: list[str] = []
    for machine in _active_machines(registry):
        role = str(machine.get("role") or "node").strip().lower()
        if role == "hub":
            name = str(machine.get("name", "")).strip()
            if name:
                names.append(name)
    return names


def resolve_hub_url_from_registry(registry: Mapping[str, Any]) -> str:
    """Return the hub dashboard URL declared in the registry.

    Pre: exactly one active machine has ``role: hub`` (callers should run
    :func:`assert_unique_hub_role` first).
    Post: non-empty URL string.
    """
    hubs = hub_machine_names(registry)
    assert len(hubs) == 1, "registry must declare exactly one hub machine"  # noqa: S101
    for machine in _active_machines(registry):
        if str(machine.get("name", "")).strip() == hubs[0]:
            url = registry_machine_url(machine).rstrip("/")
            if not url:
                raise RuntimeError(f"Hub machine '{hubs[0]}' has no dashboard_url")
            return url
    raise RuntimeError(f"Hub machine '{hubs[0]}' missing from registry")


def assert_unique_hub_role(registry: Mapping[str, Any]) -> None:
    """Fail fast when more than one machine claims ``role: hub`` (#2022, DbC)."""
    hubs = hub_machine_names(registry)
    if len(hubs) > 1:
        raise RuntimeError(
            f"Fleet topology error: {len(hubs)} machines declare role=hub "
            f"({', '.join(hubs)}). Demote all but one to role=node in machine_registry.yml."
        )
    if not hubs:
        log.warning(
            "Machine registry declares no hub machine (role=hub); "
            "spoke nodes cannot derive HUB_URL until one is configured."
        )


def apply_fleet_identity_from_registry(
    registry: Mapping[str, Any],
    *,
    hub_url_env: str | None,
    machine_role_env: str | None,
    display_name: str | None = None,
    platform_node: str | None = None,
    runner_aliases: str | Iterable[str] | None = None,
) -> FleetIdentity:
    """Derive ``MACHINE_ROLE`` and ``HUB_URL`` from the registry with env overrides.

    ``HUB_URL`` env wins when set. ``MACHINE_ROLE`` env wins when set. Otherwise
    the local machine entry (matched by name/alias) supplies the role, and spokes
    point at the registry hub's ``dashboard_url``.
    """
    from machine_registry import resolve_local_identity

    assert_unique_hub_role(registry)
    hubs = tuple(hub_machine_names(registry))

    if machine_role_env and str(machine_role_env).strip():
        machine_role = str(machine_role_env).strip().lower()
        role_source = "env"
    else:
        identity = resolve_local_identity(
            display_name,
            None,
            dict(registry),
        )
        machine_role = str(identity.get("role") or "node").strip().lower()
        role_source = "registry" if identity.get("registry_match") not in (None, "unregistered") else "default"

    hub_url: str | None = None
    hub_source = "none"
    env_hub = (hub_url_env or "").strip().rstrip("/")
    if env_hub:
        hub_url = env_hub
        hub_source = "env"
    elif machine_role != "hub" and hubs:
        hub_url = resolve_hub_url_from_registry(registry)
        hub_source = "registry"

    return FleetIdentity(
        machine_role=machine_role,
        machine_role_source=role_source,
        hub_url=hub_url,
        hub_url_source=hub_source,
        hub_machine_names=hubs,
    )


def _local_dashboard_url_candidates(
    registry: Mapping[str, Any],
    *,
    display_name: str | None = None,
    platform_node: str | None = None,
    runner_aliases: str | Iterable[str] | None = None,
    local_port: int = 8321,
) -> set[str]:
    identities = current_identity_names(
        display_name=display_name,
        platform_node=platform_node,
        runner_aliases=runner_aliases,
    )
    urls: set[str] = {
        f"http://127.0.0.1:{local_port}".rstrip("/").lower(),
        f"http://localhost:{local_port}".rstrip("/").lower(),
    }
    for machine in _active_machines(registry):
        if not registry_machine_matches_current_dashboard(machine, identities):
            continue
        machine_url = registry_machine_url(machine).strip().rstrip("/").lower()
        if machine_url:
            urls.add(machine_url)
        for node in machine.get("tailscale_nodes", []) or []:
            if not isinstance(node, Mapping):
                continue
            ip = str(node.get("ip", "")).strip()
            if ip:
                urls.add(f"http://{ip}:{local_port}".rstrip("/").lower())
    return urls


def assert_fleet_identity_no_self_loop(
    identity: FleetIdentity,
    registry: Mapping[str, Any],
    *,
    display_name: str | None = None,
    platform_node: str | None = None,
    runner_aliases: str | Iterable[str] | None = None,
    local_port: int = 8321,
) -> None:
    """Fail closed when the hub host is mislabeled a node or HUB_URL loops locally."""
    identities = current_identity_names(
        display_name=display_name,
        platform_node=platform_node,
        runner_aliases=runner_aliases,
    )
    hub_labels = {normalize_fleet_label(name) for name in identity.hub_machine_names}
    if hub_labels & identities and identity.machine_role != "hub":
        raise RuntimeError(
            "Fleet topology error: this host is the registered hub "
            f"({', '.join(identity.hub_machine_names)}) but MACHINE_ROLE resolved to "
            f"'{identity.machine_role}' (source={identity.machine_role_source}). "
            "Remove MACHINE_ROLE from systemd and ~/.config/runner-dashboard/env."
        )
    hub_target = (identity.hub_url or "").strip().rstrip("/").lower()
    if hub_target and hub_target in _local_dashboard_url_candidates(
        registry,
        display_name=display_name,
        platform_node=platform_node,
        runner_aliases=runner_aliases,
        local_port=local_port,
    ):
        raise RuntimeError(
            f"Fleet topology error: HUB_URL points at this dashboard ({identity.hub_url}), "
            "which causes hub-spoke self-proxy loops. Unset HUB_URL on the hub host."
        )


def normalize_hub_url_for_compare(url: str) -> str:
    """Normalize dashboard URLs for hub-equality checks (#2058)."""
    return str(url or "").strip().rstrip("/").lower()


def assert_hub_url_env_matches_registry(
    registry: Mapping[str, Any],
    *,
    hub_url_env: str | None,
    allow_registry_override: bool,
) -> None:
    """Fail closed when host-local ``HUB_URL`` disagrees with the registry hub (#2058).

    Pre: registry passes :func:`assert_unique_hub_role` when a hub is declared.
    Post: returns silently when ``hub_url_env`` is empty, matches the registry hub,
    or ``allow_registry_override`` is true; otherwise raises ``RuntimeError``.
    """
    env_hub = normalize_hub_url_for_compare(hub_url_env or "")
    if not env_hub or allow_registry_override:
        return
    assert_unique_hub_role(registry)
    try:
        registry_hub = normalize_hub_url_for_compare(resolve_hub_url_from_registry(registry))
    except (AssertionError, RuntimeError):
        return
    if env_hub == registry_hub:
        return
    canonical = resolve_hub_url_from_registry(registry)
    displayed = str(hub_url_env or "").strip().rstrip("/")
    raise RuntimeError(
        f"Fleet topology error: HUB_URL={displayed} disagrees with "
        f"machine_registry.yml hub ({canonical}). Remove HUB_URL from "
        "~/.config/runner-dashboard/env to derive the hub from the registry, or set "
        "HUB_URL_REGISTRY_OVERRIDE=1 after confirming the override is intentional."
    )


# Public alias for callers that name the resolution step explicitly (issue #2022).
derive_machine_role_and_hub_url = apply_fleet_identity_from_registry


def assert_valid_active_registry(registry: Mapping[str, Any]) -> None:
    """Validate that every non-retired machine and pool has a valid dashboard URL and config.

    Issue #1169: Fail fast when an active machine or runner pool has no resolvable
    dashboard URL, invalid URL format, or conflicting ports with other pools.
    Issue #2022: Fail fast when multiple machines declare ``role: hub``.
    """
    if not isinstance(registry, Mapping):
        raise RuntimeError("Machine registry must be a mapping")

    assert_unique_hub_role(registry)

    machines = registry.get("machines", [])
    if not isinstance(machines, list):
        raise RuntimeError("Machine registry field 'machines' must be a list")

    for machine in machines:
        if not isinstance(machine, Mapping):
            continue
        if machine.get("retired"):
            continue

        name = str(machine.get("name", "")).strip()
        if not name:
            raise RuntimeError(f"Active machine missing name: {machine}")

        url = registry_machine_url(machine)
        if not url:
            raise RuntimeError(f"Active machine '{name}' has no resolvable dashboard URL or Tailscale IP")

        try:
            from security import validate_fleet_node_url

            validate_fleet_node_url(url)
        except Exception as exc:
            raise RuntimeError(f"Active machine '{name}' has invalid dashboard URL {url!r}: {exc}") from exc

        pools_raw = machine.get("runner_pools")
        if pools_raw is not None:
            if not isinstance(pools_raw, list):
                raise RuntimeError(f"Active machine '{name}' runner_pools must be a list")
            active_pools = [p for p in pools_raw if isinstance(p, Mapping) and not p.get("retired")]
            if pools_raw and not active_pools:
                raise RuntimeError(
                    f"Active machine '{name}' defines runner pools but all are retired. "
                    "Either activate a pool or retire the machine."
                )

            ports_seen: dict[int, str] = {}
            for pool in active_pools:
                pool_name = str(pool.get("name", "")).strip()
                if not pool_name:
                    raise RuntimeError(f"Active runner pool under '{name}' missing name: {pool}")
                pool_url = str(pool.get("dashboard_url") or "").strip()
                if not pool_url:
                    raise RuntimeError(f"Active runner pool '{pool_name}' under '{name}' missing dashboard_url")
                try:
                    from security import validate_fleet_node_url

                    validate_fleet_node_url(pool_url)
                except Exception as exc:
                    raise RuntimeError(
                        f"Active runner pool '{pool_name}' under '{name}' has invalid dashboard_url {pool_url!r}: {exc}"
                    ) from exc

                port = _pool_url_port(pool_url)
                if port is not None:
                    if port in ports_seen:
                        raise RuntimeError(
                            f"Active runner pools under '{name}' have duplicate port {port} "
                            f"('{ports_seen[port]}', '{pool_name}'). "
                            "Did you forget to mark the retired pool as 'retired: true'?"
                        )
                    ports_seen[port] = pool_name
