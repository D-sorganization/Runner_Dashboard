"""Non-hub nodes wait until the hub is healthy at the target commit."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


def hub_commit_from_status(payload: dict[str, Any], hub_pool_name: str | None = None) -> str | None:
    """Extract hub deployment git SHA from a fleet status payload."""
    if not isinstance(payload, dict):
        return None
    for key, node in payload.items():
        if key.startswith("_"):
            continue
        if not isinstance(node, dict):
            continue
        role = str(node.get("_role") or "")
        if role == "hub" or (hub_pool_name and key == hub_pool_name):
            for dep_key in ("deployment",):
                deployment = node.get(dep_key)
                if isinstance(deployment, dict):
                    sha = str(deployment.get("git_sha") or "").lower()
                    if len(sha) == 40:
                        return sha
            health = node.get("health")
            if isinstance(health, dict):
                dep = health.get("deployment")
                if isinstance(dep, dict):
                    sha = str(dep.get("git_sha") or "").lower()
                    if len(sha) == 40:
                        return sha
    return None


def hub_ready_for_commit(
    fleet_status: dict[str, Any],
    target_sha: str,
    *,
    hub_pool_name: str | None = None,
) -> tuple[bool, str]:
    sha = hub_commit_from_status(fleet_status, hub_pool_name)
    if sha is None:
        return False, "hub deployment sha not found in fleet status"
    if sha != target_sha.lower():
        return False, f"hub at {sha[:7]}, want {target_sha[:7]}"
    return True, f"hub at {sha[:7]}"


def fetch_fleet_status(hub_url: str, token: str | None, *, timeout: float = 20.0) -> dict[str, Any]:
    url = f"{hub_url.rstrip('/')}/api/fleet/status?exclude_pools=true"
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(str(exc)) from exc
    return payload if isinstance(payload, dict) else {}
