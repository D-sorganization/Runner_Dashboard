"""Session-start skill fetch + export (#2157 slice 4)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from staff.skills.export import write_exports
from staff.skills.registry import get_skill, list_skills

log = logging.getLogger("dashboard.staff.skills.delivery")

_EXPORT_ENV = "RUNNER_DASHBOARD_SKILLS_EXPORT_DIR"


def export_dir(provider: str) -> Path:
    override = os.environ.get(_EXPORT_ENV, "").strip()
    base = Path(override).expanduser() if override else Path.home() / ".config" / "runner-dashboard" / "skill-exports"
    return (base / provider.strip().lower()).expanduser()


def deliver_skills_for_role(role: str, *, provider: str = "cursor") -> dict[str, Any]:
    """Index skills for ``role`` and write provider-native exports. Post: summary dict."""
    role = role.strip()
    if not role:
        return {"exported": 0, "skipped": True, "reason": "empty role"}
    summaries = list_skills(role=role)
    details = []
    for row in summaries:
        detail = get_skill(row.name, version=row.version)
        if detail is not None:
            details.append(detail)
    dest = export_dir(provider)
    files = write_exports(details, dest, provider=provider)
    log.info("exported %d skills for role=%s provider=%s -> %s", len(files), role, provider, dest)
    return {
        "exported": len(details),
        "export_dir": str(dest),
        "files": [f.relative_path for f in files],
        "pins": [d.pin for d in details],
    }
