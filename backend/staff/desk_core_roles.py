"""Hub Desk staff roles that must pass dispatch dry-run self-tests (#3847)."""

from __future__ import annotations

import os

# Scheduled / persona roles that run on the Desk hub and must complete a dry-run dispatch.
DEFAULT_DESK_CORE_STAFF_ROLES: tuple[str, ...] = (
    "bob",
    "night-watch",
    "issue-remediator",
    "pr-remediator",
    "cartographer",
    "board-secretary",
    "sanitation",
)


def desk_core_staff_roles() -> tuple[str, ...]:
    raw = os.environ.get("STAFF_DESK_CORE_ROLES", "").strip()
    if not raw:
        return DEFAULT_DESK_CORE_STAFF_ROLES
    parts = tuple(r.strip() for r in raw.split(",") if r.strip())
    return parts or DEFAULT_DESK_CORE_STAFF_ROLES
