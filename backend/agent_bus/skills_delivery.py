"""Fetch skills from the dashboard API at session start (#2157 slice 4)."""

from __future__ import annotations

import logging
from typing import Any

from fleet_client import FleetAPIError, FleetClient
from staff.skills.contract import SkillAttachment, SkillDetail
from staff.skills.delivery import export_dir
from staff.skills.export import write_exports

log = logging.getLogger("dashboard.agent_bus.skills_delivery")


def _detail_from_api(payload: dict[str, Any]) -> SkillDetail:
    attachments = tuple(
        SkillAttachment(
            path=str(a.get("path") or ""),
            kind=str(a.get("kind") or "other"),
            content=str(a.get("content") or ""),
            content_hash=str(a.get("content_hash") or ""),
        )
        for a in payload.get("attachments") or []
        if isinstance(a, dict)
    )
    return SkillDetail(
        name=str(payload["name"]),
        version=str(payload["version"]),
        description=str(payload.get("description") or ""),
        when_to_use=str(payload.get("when_to_use") or ""),
        roles=tuple(payload.get("roles") or []),
        repos=tuple(payload.get("repos") or []),
        task_types=tuple(payload.get("task_types") or []),
        content_hash=str(payload.get("content_hash") or ""),
        pin=str(payload.get("pin") or ""),
        source_path=str(payload.get("source_path") or ""),
        package_dir="",
        body=str(payload.get("body") or ""),
        attachments=attachments,
    )


def export_skills_at_session_start(client: FleetClient, *, role: str, provider: str) -> dict[str, Any]:
    """Pre: ``client.token`` can read staff skills. Post: exports written or skipped."""
    try:
        listed = client.staff_skills_list(role=role)
    except FleetAPIError as exc:
        if exc.status in (0, 401, 403):
            log.debug("skills export skipped: status=%s", exc.status)
            return {"skills_exported": 0, "skills_offline": exc.status == 0}
        raise
    rows = listed.get("skills") if isinstance(listed, dict) else None
    if not isinstance(rows, list):
        return {"skills_exported": 0}
    details: list[SkillDetail] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        full = client.staff_skill_get(str(row["name"]), version=row.get("version"))
        if isinstance(full, dict):
            details.append(_detail_from_api(full))
    dest = export_dir(provider)
    files = write_exports(details, dest, provider=provider)
    return {
        "skills_exported": len(details),
        "skills_export_dir": str(dest),
        "skills_files": [f.relative_path for f in files],
    }
