"""Hub-side minting of staff run GitHub tokens for registered fleet nodes (#2051)."""

from __future__ import annotations

import logging
from typing import Any

import dashboard_config
from dashboard_config import ORG
from fastapi import HTTPException
from machine_registry import (
    build_machine_registry_index,
    canonical_node_token,
    load_machine_registry,
    machines_claiming_node_token,
)
from security import validate_repo_slug
from staff.github_run_env import StaffGithubToken, mint_staff_run_github_token_local

from staff.roles import RoleSpec, load_roles

log = logging.getLogger("dashboard.staff.fleet_run_token")


def full_repo_slug(repo: str) -> str:
    """Return ``ORG/<slug>`` for a bare or fully qualified repository name."""
    raw = str(repo or "").strip()
    if not raw:
        return ""
    owner, _, name = raw.rpartition("/")
    if owner and owner != ORG:
        raise HTTPException(status_code=403, detail="repository outside organization scope")
    slug = validate_repo_slug(name or raw)
    return f"{ORG}/{slug}"


def _machine_named(registry: dict[str, Any], name: str) -> dict[str, Any] | None:
    """The top-level registry machine called ``name``, or None."""
    for machine in registry.get("machines", []) or []:
        if isinstance(machine, dict) and str(machine.get("name", "")) == name:
            return machine
    return None


def _role_of(entry: dict[str, Any]) -> str:
    return str(entry.get("role", "node")).strip().lower()


def assert_registered_fleet_node(node_header: str) -> str:
    """Pre: ``node_header`` names the calling fleet node. Post: canonical registry name.

    The header is matched on ``machine_registry.canonical_node_token`` — the same
    token the registry index is keyed on — so names with '-', '_' or space resolve
    (WP19 F-03). Malformed, unknown, retired and colliding names are refused.
    """
    name = str(node_header or "").strip()
    if not name:
        raise HTTPException(status_code=403, detail="X-Fleet-Node header is required")
    try:
        token = canonical_node_token(name)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="invalid X-Fleet-Node header") from exc
    registry = load_machine_registry()
    owners = machines_claiming_node_token(registry, token)
    if len(owners) > 1:
        log.warning("fleet run-token request refused: node token %s matches several registry machines", token)
        raise HTTPException(status_code=403, detail="ambiguous fleet node name")
    entry = build_machine_registry_index(registry).get(token)
    # A runner-pool entry carries neither its machine's ``retired`` flag nor its role, so
    # the machine that owns the token is checked as well: a retired machine's pool is
    # retired, and the hub's pool is the hub.
    machine = _machine_named(registry, owners[0]) if owners else None
    if entry is None or machine is None or entry.get("retired") or machine.get("retired"):
        raise HTTPException(status_code=403, detail="unregistered fleet node")
    if "hub" in {_role_of(entry), _role_of(machine)}:
        raise HTTPException(status_code=403, detail="hub cannot request spoke run tokens")
    canonical = str(entry.get("name") or name).strip()
    log.debug("fleet run-token request from registered node %s", canonical)
    return canonical


def _role_by_name(role_name: str) -> RoleSpec:
    roles = load_roles()
    role = roles.get(role_name.strip())
    if role is None:
        raise HTTPException(status_code=422, detail=f"unknown staff role {role_name}")
    return role


def assert_repositories_allowed_for_role(role: RoleSpec, repositories: list[str]) -> str:
    """Validate repo list against the role; return the primary repo slug for minting."""
    if not repositories:
        raise HTTPException(status_code=422, detail="repositories must be a non-empty list")
    allowed = {full_repo_slug(r) for r in role.repos} if role.repos else None
    normalized: list[str] = []
    for repo in repositories:
        full = full_repo_slug(repo)
        if not full:
            raise HTTPException(status_code=422, detail="repository name is required")
        if allowed is not None and full not in allowed:
            raise HTTPException(status_code=403, detail="repository outside run scope")
        normalized.append(full)
    return normalized[0]


def mint_fleet_run_github_token(
    *,
    run_id: str,
    role_name: str,
    repositories: list[str],
    caller_node: str,
) -> StaffGithubToken:
    """Mint a GitHub installation token on the hub for one active staff run on a spoke.

    Pre: this process is the fleet hub with GitHub App credentials configured.
    Post: returns a non-empty installation token scoped to the requested repository.
    """
    if dashboard_config.MACHINE_ROLE != "hub":
        raise HTTPException(status_code=404, detail="run-token minting is only available on the fleet hub")
    rid = str(run_id or "").strip()
    if not rid:
        raise HTTPException(status_code=422, detail="run_id is required")
    assert_registered_fleet_node(caller_node)
    role = _role_by_name(role_name)
    repo = assert_repositories_allowed_for_role(role, repositories)
    return mint_staff_run_github_token_local(role, repo)
