"""Bearer-only auth for ``POST /api/staff/{role}/run`` (#2053).

Fleet and service tokens are accepted (including ``HUB_FLEET_TOKEN`` as a
fleet-peer principal). Loopback development auth does **not** substitute for a
bearer on dispatch: even with ``DASHBOARD_LOOPBACK_AUTH=1``, callers must send
``Authorization: Bearer …`` so dry runs and dispatches audit a real principal.
"""

from __future__ import annotations

import functools
import hmac
import os

from fastapi import Depends, HTTPException, Request
from identity import (
    Principal,
    _resolve_principal_optional,
    auth_cookie,
    auth_header,
    principal_has_scope,
    require_principal,
)

_DISPATCH_SCOPE = "staff.dispatch"


@functools.cache
def require_staff_run_dispatch():
    """FastAPI dependency: ``staff.dispatch`` scope via bearer or session only."""

    def checker(
        request: Request,
        header_token: str | None = Depends(auth_header),
        cookie_token: str | None = Depends(auth_cookie),
    ) -> Principal:
        overrides = getattr(request.app, "dependency_overrides", {})
        if require_principal in overrides:
            override_fn = overrides[require_principal]
            prin = override_fn() if callable(override_fn) else override_fn
            if isinstance(prin, Principal):
                if principal_has_scope(prin, _DISPATCH_SCOPE):
                    return prin
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "Authorization failed",
                        "required_scope": _DISPATCH_SCOPE,
                        "principal": prin.id,
                    },
                )

        prin = _resolve_principal_optional(request, header_token)
        hub_token = os.environ.get("HUB_FLEET_TOKEN", "")
        if prin is None and hub_token and header_token and header_token.startswith("Bearer "):
            if hmac.compare_digest(header_token[7:], hub_token):
                prin = Principal(id="fleet-peer", type="bot", name="Fleet Peer", roles=["fleet-peer"])

        if prin is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if principal_has_scope(prin, _DISPATCH_SCOPE):
            return prin
        raise HTTPException(
            status_code=403,
            detail={
                "error": "Authorization failed",
                "required_scope": _DISPATCH_SCOPE,
                "principal": prin.id,
            },
        )

    return checker
