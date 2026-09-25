"""Scoped per-run credentials for staff runs (issue #1310, SC-E2).

Mints short-lived, fine-grained Bearer tokens for staff runs so roles can invoke
allowed fleet actions against dashboard routes. Token scopes are the intersection
of the role's declared ``fleet_actions`` (SC-E1) and the dashboard's ``ACTION_POLICY``.
Tokens expire at run deadline (TTL) and are revoked upon run termination or orphan
reconciliation. Tokens never bypass approval gates.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from collections.abc import Iterable
from typing import Final

from identity import SCOPE_PRESETS, Principal, identity_manager

log = logging.getLogger("dashboard.staff.tokens")

# Fixed catalog of fleet actions and the scopes they require (Runner_Dashboard SC-E2 / RM#1734 SC-E1)
FLEET_ACTION_SCOPES: Final[dict[str, tuple[str, ...]]] = {
    "runner.start": ("runners.control", "fleet.maintain"),
    "runner.stop": ("runners.control", "fleet.maintain"),
    "runner.restart": ("runners.control", "fleet.maintain"),
    "runner.scale": ("runners.control", "fleet.control", "fleet.maintain"),
    "fleet.node_up": ("fleet.control", "fleet.maintain"),
    "fleet.node_down": ("fleet.control", "fleet.maintain"),
    "queue.purge_stale": ("workflows.control", "fleet.maintain"),
    "run.cancel": ("workflows.control", "fleet.maintain"),
    "run.rerun": ("workflows.control", "fleet.maintain"),
    "queue.diagnose": ("runners.control", "fleet.maintain"),
    "host.vhdx_compact": ("system.control", "fleet.maintain"),
    "dashboard.restart": ("system.control", "fleet.maintain"),
}

ACTION_POLICY: Final[frozenset[str]] = frozenset(FLEET_ACTION_SCOPES.keys())

_active_run_tokens: dict[str, str] = {}  # run_id -> principal_id


def resolve_run_scopes(fleet_actions: Iterable[str]) -> list[str]:
    """Calculate granted API scopes as the intersection of role actions and ACTION_POLICY."""
    granted_actions = set(fleet_actions) & ACTION_POLICY
    scopes: set[str] = set()
    for action in granted_actions:
        scopes.update(FLEET_ACTION_SCOPES[action])
    return sorted(scopes)


def mint_run_token(
    role: str,
    run_id: str,
    fleet_actions: Iterable[str],
    ttl_seconds: float,
) -> str:
    """Mint a short-lived token for a staff run.

    Principal ID: ``staff:<role>:<run_id>``
    Scopes: intersection of role fleet_actions and ACTION_POLICY
    TTL: run deadline in seconds
    """
    if not role or not run_id:
        raise ValueError("role and run_id are required to mint a staff run token")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")

    scopes = resolve_run_scopes(fleet_actions)
    principal_id = f"staff:{role}:{run_id}"
    role_preset = f"staff-run:{run_id}"

    principal = Principal(
        id=principal_id,
        type="bot",
        name=f"Staff run {run_id} ({role})",
        roles=[role_preset],
        scopes=scopes,
    )

    SCOPE_PRESETS[role_preset] = list(scopes)

    raw_token = f"run_{secrets.token_urlsafe(32)}"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires_at = time.time() + ttl_seconds

    identity_manager.add_ephemeral_token(
        token_hash=token_hash,
        principal=principal,
        expires_at=expires_at,
        name=f"staff-run-{run_id}",
    )
    _active_run_tokens[run_id] = principal_id

    log.info(
        "Minted staff run token for %s (principal=%s, scopes=%s, ttl=%.1fs)",
        run_id,
        principal_id,
        scopes,
        ttl_seconds,
    )
    return raw_token


def revoke_run_token(run_id: str) -> bool:
    """Revoke any active ephemeral tokens and credentials for run_id."""
    principal_id = _active_run_tokens.pop(run_id, None) or f"staff:*:{run_id}"
    role_preset = f"staff-run:{run_id}"
    SCOPE_PRESETS.pop(role_preset, None)

    identity_manager.revoke_ephemeral_principal(principal_id)
    # Also clean up any matching by prefix/suffix if principal_id had wildcard or exact match
    to_remove = [
        pid
        for pid in list(identity_manager._ephemeral_principals.keys())
        if pid.endswith(f":{run_id}") or pid == principal_id
    ]
    for pid in to_remove:
        identity_manager.revoke_ephemeral_principal(pid)

    log.debug("Revoked staff run token for %s", run_id)
    return True
