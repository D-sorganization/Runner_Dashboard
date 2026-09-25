"""Short-lived, scoped credentials for staff runs (issue #1310, SC-E2).

Staff runs get STAFF_RUN_ID but no long-lived admin credential.
This module mints per-run ephemeral tokens:
- Principal format: `staff:<role>:<run_id>`
- Scopes: intersection of role's `fleet_actions` (SC-E1) and action policy
- TTL: bounded by run deadline
- Injected as: `FLEET_API_TOKEN` environment variable
- Revoked: at run end (succeeded, failed, cancelled) and on restart reconcile (SC-A4)
- Error handling: minting failure fails run as `failure_class=workspace_error` before CLI start
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from identity import Principal, identity_manager

log = logging.getLogger("dashboard.staff.credential")

# The 12 allowlisted fleet actions supported in the D-sorganization fleet
# matching Repository_Management staff/schema.json and SC-E1.
FLEET_ACTIONS: tuple[str, ...] = (
    "runner.start",
    "runner.stop",
    "runner.restart",
    "runner.scale",
    "fleet.node_up",
    "fleet.node_down",
    "queue.purge_stale",
    "run.cancel",
    "run.rerun",
    "queue.diagnose",
    "host.vhdx_compact",
    "dashboard.restart",
)

# Action policy: mapping from each fleet action to the API scopes
# required to execute the action through the Runner Dashboard endpoints.
ACTION_POLICY_SCOPES: dict[str, tuple[str, ...]] = {
    "runner.start": ("runners.control",),
    "runner.stop": ("runners.control",),
    "runner.restart": ("runners.control",),
    "runner.scale": ("runners.control", "fleet.control"),
    "fleet.node_up": ("fleet.control",),
    "fleet.node_down": ("fleet.control",),
    "queue.purge_stale": ("workflows.control", "remediation.dispatch"),
    "run.cancel": ("workflows.control", "staff.cancel"),
    "run.rerun": ("workflows.dispatch", "tests.rerun"),
    "queue.diagnose": ("runners.control", "staff.read"),
    "host.vhdx_compact": ("fleet.control", "fleet.maintain"),
    "dashboard.restart": ("fleet.control", "system.control"),
}


def compute_staff_scopes(role_fleet_actions: Iterable[str] | None) -> list[str]:
    """Compute API scopes for a staff role by intersecting fleet_actions with action policy.

    Pre: role_fleet_actions is an iterable of action names or None.
    Post: returns a sorted, unique list of allowed API scopes.
    """
    if not role_fleet_actions:
        return []
    action_set = set(role_fleet_actions)
    allowed_actions = action_set.intersection(ACTION_POLICY_SCOPES.keys())
    if not allowed_actions:
        return []

    granted_scopes: set[str] = set()
    for action in allowed_actions:
        for scope in ACTION_POLICY_SCOPES[action]:
            granted_scopes.add(scope)

    return sorted(granted_scopes)


def staff_principal_id(role: str, run_id: str) -> str:
    """Format the canonical staff principal ID.

    Pre: role and run_id are non-empty strings.
    Post: returns 'staff:<role>:<run_id>'.
    """
    role_clean = role.strip()
    run_clean = run_id.strip()
    if not role_clean or not run_clean:
        raise ValueError("Both role and run_id must be non-empty strings")
    return f"staff:{role_clean}:{run_clean}"


def mint_staff_credentials(
    role: str | Any,
    run_id: str,
    fleet_actions: Iterable[str] | None = None,
    ttl_seconds: float = 3600.0,
) -> tuple[str, list[str]]:
    """Mint short-lived, scoped credentials for a staff execution run.

    Pre:
        - role is a non-empty string or RoleSpec with name.
        - run_id is a non-empty string.
    Post:
        - returns (raw_token, scopes) where raw_token starts with 'stf_'.
        - principal 'staff:<role>:<run_id>' is registered in identity_manager.
    """
    role_name = getattr(role, "name", str(role))
    if fleet_actions is None and hasattr(role, "fleet_actions"):
        fleet_actions = getattr(role, "fleet_actions", None)

    prin_id = staff_principal_id(role_name, run_id)
    scopes = compute_staff_scopes(fleet_actions)
    ttl = float(ttl_seconds)

    principal = Principal(
        id=prin_id,
        type="bot",
        name=f"Staff run {role}:{run_id}",
        roles=[],
        scopes=scopes,
    )
    identity_manager.principals[prin_id] = principal
    identity_manager.save_principals()

    try:
        raw_token = identity_manager.mint_ephemeral_token(
            principal_id=prin_id,
            name=f"run-{run_id}",
            expires_in_seconds=ttl,
            prefix="stf_",
        )
    except Exception:
        identity_manager.principals.pop(prin_id, None)
        identity_manager.save_principals()
        raise

    log.info(
        "Minted staff run credential principal=%s scopes=%s ttl=%.1fs",
        prin_id,
        scopes,
        ttl,
    )
    return raw_token, scopes


def revoke_staff_credentials(role_or_principal: str, run_id: str | None = None) -> int:
    """Revoke all tokens and remove the temporary principal for a staff run.

    Pre: role_or_principal is a non-empty string.
    Post: any tokens for this principal are removed; principal is deleted.
    """
    if run_id is not None:
        prin_id = staff_principal_id(role_or_principal, run_id)
    elif role_or_principal.startswith("staff:"):
        prin_id = role_or_principal.strip()
    else:
        raise ValueError("Must provide either a full principal ID or (role, run_id)")

    revoked = identity_manager.revoke_principal_tokens(prin_id, delete_principal=True)
    log.info("Revoked %d tokens for staff principal %s", revoked, prin_id)
    return revoked


def revoke_orphan_staff_credentials(orphaned_run_ids: Iterable[str]) -> int:
    """Revoke any dangling tokens for orphaned staff runs during restart reconcile.

    Pre: orphaned_run_ids is an iterable of run_id strings.
    Post: any tokens matching staff:*:<run_id> are revoked and their principals deleted.
    """
    run_set = {rid.strip() for rid in orphaned_run_ids if rid and rid.strip()}
    if not run_set:
        return 0

    revoked_total = 0
    matching_principals = set()
    for t in list(identity_manager.tokens):
        if t.principal_id.startswith("staff:"):
            parts = t.principal_id.split(":")
            if len(parts) >= 3 and parts[2] in run_set:
                matching_principals.add(t.principal_id)

    for prin_id in matching_principals:
        revoked_total += identity_manager.revoke_principal_tokens(prin_id, delete_principal=True)

    log.info(
        "Orphan reconciliation revoked %d tokens across %d staff principals",
        revoked_total,
        len(matching_principals),
    )
    return revoked_total
