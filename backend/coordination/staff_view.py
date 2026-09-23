"""Staff runs in flight, shaped for the coordination views (issue #1229).

Reuses the Staff Hub fan-out (``staff.fleet.aggregate_board``) so the
coordination API sees the same fleet-wide in-flight list as ``/api/staff/summary``.
"""

from __future__ import annotations

import logging
from typing import Any

from staff import fleet as staff_fleet
from staff.runner import get_runner

log = logging.getLogger("dashboard.coordination")


def _row(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run.get("id"),
        "role": run.get("role"),
        "provider": run.get("provider"),
        "machine": run.get("machine"),
        "repo": run.get("repo"),
        "target": run.get("target_ref"),
        "status": run.get("status"),
        "started_at": run.get("started_at"),
        "source": "staff",
    }


async def staff_runs(repo: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """``(runs, warnings)`` for every running/queued staff run fleet-wide, optionally only ``repo``."""
    try:
        runner = get_runner()
        active = runner.store.active_runs()
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
