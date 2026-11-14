"""RunStore usage aggregates (split from ``staff.store`` for the 500-line cap)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from staff.store import RunStore

USAGE_GROUPS = ("provider", "role", "day")
_USAGE_GROUP_SQL = {"provider": "provider", "role": "role", "day": "substr(created_at, 1, 10)"}


def usage_by(store: RunStore, group: str = "provider", since: str | None = None) -> list[dict[str, Any]]:
    """Aggregate runs by ``provider``, ``role`` or ``day`` (issue #1200)."""
    assert group in USAGE_GROUPS, group  # noqa: S101
    key_sql = _USAGE_GROUP_SQL[group]
    where = "WHERE created_at >= ?" if since else ""
    params: tuple[Any, ...] = (since,) if since else ()
    sql = (
        f"SELECT {key_sql} AS key, COUNT(*) AS runs, COALESCE(SUM(cost_usd), 0) AS cost_usd, "
        "COALESCE(SUM(input_tokens), 0) AS input_tokens, COALESCE(SUM(output_tokens), 0) AS output_tokens, "
        "COALESCE(SUM(CASE WHEN started_at IS NOT NULL AND ended_at IS NOT NULL "
        "THEN (julianday(ended_at) - julianday(started_at)) * 86400.0 ELSE 0 END), 0) AS wall_seconds "
        f"FROM runs {where} GROUP BY key ORDER BY key"  # noqa: S608
    )
    with store._lock:  # noqa: SLF001
        rows = store._conn.execute(sql, params).fetchall()  # noqa: SLF001
    return [
        {
            "key": str(r["key"]),
            "runs": int(r["runs"]),
            "cost_usd": round(float(r["cost_usd"]), 6),
            "input_tokens": int(r["input_tokens"]),
            "output_tokens": int(r["output_tokens"]),
            "wall_seconds": round(max(0.0, float(r["wall_seconds"])), 1),
        }
        for r in rows
    ]
