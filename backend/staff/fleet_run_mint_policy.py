"""Run-bound mint policy for hub fleet run credentials (RM#2855 / F-01, F-02)."""

from __future__ import annotations

import logging

from fastapi import HTTPException
from identity import Principal, principal_has_scope
from staff.adapters import ADAPTERS
from staff.roles import RoleSpec, load_roles
from staff.store import ACTIVE_STATUSES, RunRecord
from staff.tokens import is_active_run_principal

log = logging.getLogger("dashboard.staff.fleet_run_mint")

FLEET_RUN_CREDENTIAL_MINT_SCOPE = "fleet.run_credential.mint"


def principal_mint_scope_denied(principal: Principal) -> bool:
    """True when this principal must never receive a run JWT (admin / wildcard)."""
    if "admin" in principal.roles:
        return True
    return principal_has_scope(principal, "*")


def load_active_run_for_mint(run_id: str) -> RunRecord:
    """Resolve an active staff run for credential minting (fail closed, opaque 403)."""
    from staff.runner import get_runner  # noqa: PLC0415

    rid = str(run_id or "").strip()
    if not rid:
        raise HTTPException(status_code=403, detail="run not found or not authorized")
    rec = get_runner().store.get_run(rid)
    if rec is None or rec.status not in ACTIVE_STATUSES:
        raise HTTPException(status_code=403, detail="run not found or not authorized")
    return rec


def lease_principal_id_for_run(rec: RunRecord) -> str:
    """RM lease agent id for the run's provider (``agent-<lease_agent>``)."""
    adapter = ADAPTERS.get(rec.provider)
    if adapter is None:
        raise HTTPException(status_code=500, detail=f"unknown provider {rec.provider!r}")
    return f"agent-{adapter.lease_agent}"


def role_spec_for_run(rec: RunRecord) -> RoleSpec:
    role = load_roles().get(rec.role)
    if role is None:
        raise HTTPException(status_code=500, detail=f"unknown staff role {rec.role!r}")
    if not role.repos:
        raise HTTPException(status_code=403, detail="role has no repository scope")
    return role


def repositories_for_run_mint(role: RoleSpec, repo: str) -> list[str]:
    """Primary repository slug list derived from the run record (fail closed when empty)."""
    raw = str(repo or "").strip()
    if not raw:
        raise HTTPException(status_code=403, detail="run has no repository scope")
    return [raw]


def assert_mint_caller_bound_to_run(
    caller: Principal,
    *,
    run_id: str,
    role: str,
    lease_principal: str,
) -> None:
    """Caller must be fleet-peer, the run's ephemeral staff principal, or the lease agent."""
    if caller.id == "fleet-peer" or "fleet-peer" in caller.roles:
        return
    if is_active_run_principal(run_id=run_id, role=role, principal_id=caller.id):
        return
    if caller.id == lease_principal:
        return
    raise HTTPException(status_code=403, detail="caller is not authorized to mint for this run")


def audit_mint(*, kind: str, run_id: str, principal_id: str, caller_id: str) -> None:
    """Audit log for mint operations (ids only, never bearer secrets)."""
    log.info(
        "fleet run credential mint kind=%s run_id=%s principal=%s caller=%s",
        kind,
        run_id,
        principal_id,
        caller_id,
    )
