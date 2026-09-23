"""Write authentication for the coordination API (issue #1229).

A write is accepted from a principal holding ``coordination.write`` (the
``bot`` and ``operator`` presets carry it — mint one bot token per agent), or
from the node's own loopback orchestrator peer when ``DASHBOARD_LOOPBACK_AUTH=1``.
Unlike ``require_orchestrator_peer`` the shared ``HUB_FLEET_TOKEN`` is not a
write credential: every remote writer is an identifiable principal.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from identity import (
    Principal,
    _is_loopback_request,
    _loopback_auth_enabled,
    _resolve_principal_optional,
    auth_header,
    principal_has_scope,
)

WRITE_SCOPE = "coordination.write"


@dataclass(frozen=True)
class Caller:
    """Who is writing. ``default_agent`` is the bot principal's id, used when a body omits ``agent``."""

    label: str
    default_agent: str | None = None


def _caller(principal: Principal) -> Caller:
    agent = principal.id if principal.type == "bot" else None
    return Caller(label=f"principal:{principal.id}", default_agent=agent)


def require_coordination_writer(
    request: Request,
    header_token: str | None = Depends(auth_header),  # noqa: B008
) -> Caller:
    """401 without credentials, 403 for a principal lacking ``coordination.write``."""
    principal = _resolve_principal_optional(request, header_token)
    if principal is not None:
        if principal_has_scope(principal, WRITE_SCOPE):
            return _caller(principal)
        raise HTTPException(
            status_code=403,
            detail={"error": "Authorization failed", "required_scope": WRITE_SCOPE, "principal": principal.id},
        )
    if _loopback_auth_enabled() and _is_loopback_request(request):
        return Caller(label="loopback-dev")
    raise HTTPException(status_code=401, detail="Authentication required")
