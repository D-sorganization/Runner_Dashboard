"""Fleet agent dispatcher router.

Hub-to-node dispatch endpoints. All mutating operations go through the
dispatch_contract allowlist so only approved actions can be triggered.
Privileged actions require an explicit DispatchConfirmation payload.

These endpoints are intentionally thin: they validate and record; actual
execution is done by the caller after receiving an accepted envelope back.
"""

from __future__ import annotations

import logging

import dispatch_contract
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/fleet/dispatch", tags=["dispatch"])

log = logging.getLogger("dashboard.dispatch")


@router.get("/actions")
async def list_dispatch_actions() -> dict:
    """Return every allowlisted action with its access level and description."""
    return {"actions": [a.to_dict() for a in dispatch_contract.ALLOWLISTED_ACTIONS.values()]}


async def _parse_envelope(request: Request) -> dispatch_contract.CommandEnvelope:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Malformed envelope: expected object")
    try:
        return dispatch_contract.CommandEnvelope.from_dict(body)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Malformed envelope: {exc}") from exc


@router.post("/validate")
async def validate_dispatch_envelope(request: Request) -> dict:
    """Validate a raw command envelope dict and return the validation result.

    The caller is responsible for providing a well-formed envelope JSON body.
    Privileged actions must include a ``confirmation`` sub-object.
    """
    envelope = await _parse_envelope(request)
    result = dispatch_contract.validate_envelope(envelope)
    audit = dispatch_contract.build_audit_log_entry(envelope, result)

    log.info(
        "validate envelope_id=%s action=%s decision=%s",
        envelope.envelope_id,
        envelope.action,
        audit.decision,
    )

    return {
        "accepted": result.accepted,
        "reason": result.reason,
        "confirmation_required": result.confirmation_required,
        "action": result.action.to_dict() if result.action else None,
        "audit": audit.to_dict(),
    }


@router.post("/submit")
async def submit_dispatch_command(request: Request) -> dict:
    """Accept a validated, confirmed command envelope and record the audit entry.

    Returns the prototype command tuple so the calling hub can preview or
    execute the approved action against the node. Actual execution is kept
    outside the dashboard process boundary for safety.
    """
    envelope = await _parse_envelope(request)
    result = dispatch_contract.validate_envelope(envelope)
    audit = dispatch_contract.build_audit_log_entry(envelope, result)

    log.info(
        "submit envelope_id=%s action=%s decision=%s requested_by=%s",
        envelope.envelope_id,
        envelope.action,
        audit.decision,
        envelope.requested_by,
    )

    if not result.accepted:
        raise HTTPException(status_code=403, detail=result.reason)

    prototype_cmd = dispatch_contract.command_preview(envelope.action, envelope.payload)
    return {
        "envelope_id": envelope.envelope_id,
        "action": envelope.action,
        "prototype_command": list(prototype_cmd),
        "audit": audit.to_dict(),
    }
