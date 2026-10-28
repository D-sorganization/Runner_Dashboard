"""Optional-integration availability for the standalone / no-fleet profile (#3180).

Reads never raise: a missing optional integration degrades to
``{"available": False, "reason": ...}`` rather than erroring, matching the
sibling-repos rule ``priorities/service.py`` already uses for the identical
Repository_Management check.
"""

from __future__ import annotations

from typing import Any

from priorities.service import NO_RM_REASON
from staff.workspace import rm_root


def check_repository_management() -> dict[str, Any]:
    """Whether a Repository_Management checkout is reachable for the lease/claim scripts."""
    root = rm_root()
    if root is None:
        return {"available": False, "reason": NO_RM_REASON}
    return {"available": True, "reason": "", "path": str(root)}


def check_all_integration_availability() -> dict[str, dict[str, Any]]:
    """Every optional integration the standalone profile can run without.

    Keyed so a future optional integration adds an entry here without
    changing the diagnostics endpoint that consumes it.
    """
    return {"repository_management": check_repository_management()}
