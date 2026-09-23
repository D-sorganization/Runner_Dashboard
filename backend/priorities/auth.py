"""Write authentication for operator directives (issue #1243).

Directive text is pasted into every staff prompt, so setting it is policy, not
coordination: it needs ``priorities.write`` (the ``operator`` preset; ``admin``
through ``*``), or the node's own loopback orchestrator peer when
``DASHBOARD_LOOPBACK_AUTH=1``. Bot principals hold ``coordination.write`` only
and are refused, as is the shared ``HUB_FLEET_TOKEN``.
"""

from __future__ import annotations

from coordination.auth import Caller
from fastapi import Depends, HTTPException, Request
from identity import (
    _is_loopback_request,
    _loopback_auth_enabled,
    _resolve_principal_optional,
    auth_header,
    principal_has_scope,
)

WRITE_SCOPE = "priorities.write"


def require_priorities_writer(
    request: Request,
    header_token: str | None = Depends(auth_header),  # noqa: B008
) -> Caller:
    """401 without credentials, 403 for a principal lacking ``priorities.write``.

    Post: ``Caller.label`` identifies the authenticated writer; it is the only ``set_by`` source.
    """
    principal = _resolve_principal_optional(request, header_token)
    if principal is not None:
        if principal_has_scope(principal, WRITE_SCOPE):
            return Caller(label=f"principal:{principal.id}")
        raise HTTPException(
            status_code=403,
            detail={"error": "Authorization failed", "required_scope": WRITE_SCOPE, "principal": principal.id},
        )
    if _loopback_auth_enabled() and _is_loopback_request(request):
        return Caller(label="loopback-dev")
    raise HTTPException(status_code=401, detail="Authentication required")
