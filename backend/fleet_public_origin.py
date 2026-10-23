"""Pure hub-origin helpers used by fleet registry adapters."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

Machine = Mapping[str, Any]
MachineEnumerator = Callable[[Mapping[str, Any]], Iterable[Machine]]
MachineUrlResolver = Callable[[Machine], str]


def dashboard_url_to_tailscale_https_origin(dashboard_url: str) -> str:
    """Map a registry ``dashboard_url`` to the Tailscale Serve HTTPS origin (no port)."""
    parts = urlsplit(dashboard_url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"Invalid dashboard_url for HTTPS origin: {dashboard_url!r}")
    return f"https://{parts.hostname}"


def collect_hub_public_origins(
    registry: Mapping[str, Any],
    *,
    active_machines: MachineEnumerator,
    machine_url: MachineUrlResolver,
) -> tuple[str, ...]:
    """Collect public origins for active hub entries using fleet-owned resolvers."""
    origins: list[str] = []
    for machine in active_machines(registry):
        role = str(machine.get("role") or "node").strip().lower()
        if role == "hub" and (url := machine_url(machine)):
            origins.append(dashboard_url_to_tailscale_https_origin(url))
    return tuple(origins)


def resolve_unique_hub_public_origin(
    registry: Mapping[str, Any],
    *,
    active_machines: MachineEnumerator,
    machine_url: MachineUrlResolver,
) -> str:
    """Resolve one canonical hub origin, returning empty when zero or many exist."""
    origins = collect_hub_public_origins(registry, active_machines=active_machines, machine_url=machine_url)
    return origins[0] if len(origins) == 1 else ""
