"""Staff runs in flight, shaped for the coordination views (issue #1229).

Reuses the Staff Hub fan-out (``staff.fleet.aggregate_board``) so the
coordination API sees the same fleet-wide in-flight list as ``/api/staff/summary``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from staff.runner import get_runner

from staff import fleet as staff_fleet

log = logging.getLogger("dashboard.coordination")

_STAFF_SESSION_TTL = timedelta(hours=8)
_ISSUE_TARGET = re.compile(r"^#(\d+)$")


def _row(run: dict[str, Any]) -> dict[str, Any]:
    target_ref = run.get("target_ref") if "target_ref" in run else run.get("target")
    return {
        "id": run.get("id"),
        "role": run.get("role"),
        "provider": run.get("provider"),
        "machine": run.get("machine"),
        "repo": run.get("repo"),
        "target": target_ref,
        "status": run.get("status"),
        "started_at": run.get("started_at"),
        "expires": run.get("expires"),
        "source": "staff",
    }


def _expires_from_started(started_at: str | None) -> str:
    if not started_at:
        return (datetime.now(UTC) + _STAFF_SESSION_TTL).isoformat().replace("+00:00", "Z")
    text = started_at.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        start = datetime.fromisoformat(text)
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
    except ValueError:
        return (datetime.now(UTC) + _STAFF_SESSION_TTL).isoformat().replace("+00:00", "Z")
    return (start + _STAFF_SESSION_TTL).isoformat().replace("+00:00", "Z")


def staff_run_as_session(run: dict[str, Any]) -> dict[str, Any]:
    """Map a staff run summary into a board-compatible session row (#4458)."""
    target = str(run.get("target") or run.get("target_ref") or "")
    issue = 0
    match = _ISSUE_TARGET.match(target)
    if match:
        issue = int(match.group(1))
    run_id = str(run.get("id") or "")
    return {
        "session": f"staff:{run_id}",
        "agent": str(run.get("role") or run.get("provider") or run_id),
        "role": run.get("role"),
        "provider": run.get("provider"),
        "machine": run.get("machine"),
        "repo": run.get("repo") or "",
        "issue": issue,
        "branch": "",
        "paths": [],
        "goals": {},
        "expires": run.get("expires") or _expires_from_started(str(run.get("started_at") or "")),
        "started_at": run.get("started_at"),
        "target": target,
        "status": run.get("status"),
        "source": "staff",
    }


async def staff_runs(repo: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """``(runs, warnings)`` for every running/queued staff run fleet-wide, optionally only ``repo``."""
    try:
        runner = get_runner()
        active = await asyncio.to_thread(runner.store.active_runs)
        local = {
            "machine": runner.machine,
            "running": [r.to_dict() for r in active if r.status == "running"],
            "queued": [r.to_dict() for r in active if r.status != "running"],
        }
        board = await staff_fleet.aggregate_board(local)
    except Exception as exc:  # noqa: BLE001 — orthogonality: staff failure must not break coordination reads
        log.warning("coordination: staff runs unavailable: %s", exc)
        return [], [f"staff runs unavailable: {exc}"]
    rows = [_row(r) for r in [*board["running"], *board["queued"]]]
    if repo:
        wanted = repo.casefold()
        rows = [r for r in rows if str(r["repo"] or "").casefold() == wanted]
    return rows, []
