"""Auth sign-in source for the Staff inbox (SC-C5).

Split from ``staff.inbox`` to keep that module under the repo's 500-line soft
cap. Collects provider authentication sign-in requirements into the single
aggregated ``auth_sign_in`` inbox item and judges its severity.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from staff.inbox_models import InboxItem
from time_utils import utc_now_iso

log = logging.getLogger("dashboard.staff.inbox")


def _auth_sign_in_severity(probes: dict[str, Any]) -> Literal["medium", "high"]:
    """Severity of the aggregated sign-in item.

    ``high`` only when no enabled, non-future provider used by a dispatchable
    role is signed in (nothing can run); otherwise ``medium``.
    """
    from agent_remediation import PROVIDER_REGISTRY
    from provider_switch import canonical_id, is_disabled

    try:
        from staff.roles import load_roles

        role_providers = {canonical_id(p) for r in load_roles().values() if r.dispatchable for p in r.providers}
    except Exception as exc:  # roles unreadable: judge by every enabled provider
        log.debug("Auth severity could not read roles: %s", exc)
        role_providers = set()

    for entry in PROVIDER_REGISTRY:
        if not entry.enabled or entry.dispatch_mode == "future":
            continue
        can_id = canonical_id(entry.dashboard_id)
        if role_providers and can_id not in role_providers:
            continue
        if is_disabled(entry.dashboard_id) or is_disabled(can_id):
            continue
        avail = probes.get(entry.dashboard_id) or {}
        if avail.get("installed") and avail.get("authenticated"):
            return "medium"
    return "high"


def _collect_auth_sign_ins() -> list[InboxItem]:
    """Collect providers requiring authentication sign-in into a single aggregated item."""
    items: list[InboxItem] = []
    try:
        from agent_remediation import PROVIDER_REGISTRY
        from agent_remediation.provider_probe import probe_provider_availability

        probes = probe_provider_availability()
        needing_sign_in: list[tuple[Any, str]] = []
        for entry in PROVIDER_REGISTRY:
            # A "future" provider is never dispatched to, so its sign-in is nobody's action.
            if not entry.enabled or entry.dispatch_mode == "future":
                continue
            avail = probes.get(entry.dashboard_id)
            if avail and avail.get("installed") and not avail.get("authenticated"):
                detail = avail.get("detail", "")
                needing_sign_in.append((entry, detail))

        if not needing_sign_in:
            return []

        count = len(needing_sign_in)
        title = "1 provider needs sign-in" if count == 1 else f"{count} providers need sign-in"
        names = ", ".join(entry.label for entry, _ in needing_sign_in)
        summary = f"Sign-in required for: {names}. Visit Settings > Credentials to authenticate."
        severity = _auth_sign_in_severity(probes)
        details = [
            {"provider": entry.dashboard_id, "label": entry.label, "reason": detail}
            for entry, detail in needing_sign_in
        ]

        items.append(
            InboxItem(
                id="auth_sign_in",
                source="auth_sign_in",
                title=title,
                summary=summary,
                severity=severity,
                created_at=utc_now_iso(),
                link="/settings#credentials",
                metadata={
                    "provider_ids": [entry.dashboard_id for entry, _ in needing_sign_in],
                    "details": details,
                },
                details=details,
            )
        )
    except Exception as exc:
        log.warning("Failed probing provider auth sign-ins: %s", exc)
        raise
    return items
