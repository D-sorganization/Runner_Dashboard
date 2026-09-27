"""Staff summary assembly for dashboard and agent fleet context (#1195, #1627).

Provides `build_staff_summary()`, the one-call brief for Barb and Orchestrator.
Split out of `routers.staff` so internal modules and chat context injectors
can call it without importing router internals (Law of Demeter).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from staff import fleet as staff_fleet
from staff.classifier import format_attention_items
from staff.runner import get_runner

__all__ = ["build_staff_summary"]


async def build_staff_summary() -> dict[str, Any]:
    """The one-call brief for Barb and Orchestrator (#1195).

    Precondition: None.
    Postcondition: returns flat payload dictionary containing what is in flight
    fleet-wide, items needing attention in the last 24 h on this node, spend today,
    provider availability per machine, active holds, late/dead scheduled roles (#1209),
    and the roster with schedules.
    """
    runner = get_runner()
    board_view = await staff_fleet.aggregate_board(staff_fleet.local_board(runner))
    since = (datetime.now(UTC) - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    recent = runner.store.list_runs(limit=200, since=since)
    counts: dict[str, int] = {}
    for run in recent:
        counts[run.status] = counts.get(run.status, 0) + 1
    attention = format_attention_items(recent)
    keep = (
        "id",
        "role",
        "provider",
        "machine",
        "repo",
        "target_ref",
        "status",
        "started_at",
        "last_line",
    )
    in_flight = [{k: r.get(k) for k in keep} for r in [*board_view["running"], *board_view["queued"]]]
    roles = runner.roles()
    return {
        "generated_at": board_view["generated_at"],
        "hub": board_view["hub"],
        "machines_online": board_view["online"],
        "machines_offline": board_view["offline"],
        "in_flight": in_flight,
        "recent_24h": counts,
        "attention": attention[:20],
        "spend_today_usd": board_view["spend_today_usd"],
        "providers": board_view["providers"],
        "holds": staff_fleet.holds_snapshot(),
        "liveness_alerts": board_view.get("liveness_alerts", []),
        "roles": [
            {
                "name": s.name,
                "title": s.title,
                "schedule": s.schedule,
                "surface": s.surface,
                "retired": s.retired,
            }
            for s in sorted(roles.values(), key=lambda s: s.name)
        ],
    }
