"""Diagnostic events for staff verification (#1516)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from staff.roles import load_roles as _default_load_roles

if TYPE_CHECKING:
    from staff.store import RunRecord, RunStore

log = logging.getLogger("dashboard.staff.verification")


def record_role_open_pr_diagnostic(
    store: RunStore,
    rec: RunRecord,
    resolver_path: str,
    *,
    roles_loader: Callable[[], dict[str, Any]] | None = None,
) -> None:
    """Telemetry for unexpected ``opens_pr=False`` verdict paths (diagnosis class E)."""
    load_roles = roles_loader or _default_load_roles
    payload: dict[str, Any] = {
        "run_id": rec.id,
        "role": rec.role,
        "resolver_path": resolver_path,
        "role_found": False,
        "role_permissions_open_pr": None,
    }
    try:
        spec = load_roles().get(rec.role)
        payload["role_found"] = spec is not None
        if spec is not None:
            payload["role_permissions_open_pr"] = bool((spec.permissions or {}).get("open_pr"))
            payload["role_surface"] = spec.surface
            payload["role_providers"] = list(spec.providers)
    except Exception as exc:  # noqa: BLE001 - diagnostics must not affect verification
        payload["role_lookup_error"] = str(exc)[:200]
    store.append_event(rec.id, "verify_diag", json.dumps(payload, sort_keys=True))
    log.warning("verification role-open-pr diagnostic: %s", payload)
