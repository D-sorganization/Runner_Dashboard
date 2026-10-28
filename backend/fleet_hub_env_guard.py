"""Fail-closed guard for stale host-local ``HUB_URL`` overrides (#2058)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fleet_autoconfig import assert_unique_hub_role, resolve_hub_url_from_registry


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
