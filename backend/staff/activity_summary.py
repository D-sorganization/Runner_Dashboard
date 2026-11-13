"""Bounded read-only activity aggregate for the node auto-deploy probe (#2353).

A small ``COUNT ... GROUP BY status`` query against the staff runs database,
never a listing of runs or messages, and no runner/provider initialisation.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class ActivitySummaryUnavailable(Exception):
    """The activity summary database could not be read safely."""


def activity_summary_counts(path: Path) -> dict[str, int]:
    try:
        resolved = path.expanduser().resolve(strict=True)
        if not resolved.is_file():
            raise ActivitySummaryUnavailable("activity summary database is not a regular file")
        uri = f"{resolved.as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=0.25) as connection:
            connection.execute("PRAGMA busy_timeout = 250")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"runs", "messages"}.issubset(tables):
                raise ActivitySummaryUnavailable("activity summary schema is unavailable")
            run_columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
            message_columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}
            if not {"status"}.issubset(run_columns) or not {"delivery", "author_kind"}.issubset(message_columns):
                raise ActivitySummaryUnavailable("activity summary schema is incomplete")
            active, queued = connection.execute(
                "SELECT "
                "SUM(CASE WHEN status IN ('queued', 'preparing', 'running') THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) "
                "FROM runs"
            ).fetchone()
            (pending_chat,) = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE author_kind != 'user' AND delivery IN ('pending', 'streaming')"
            ).fetchone()
    except ActivitySummaryUnavailable:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ActivitySummaryUnavailable(f"could not read activity summary state: {exc}") from exc
    return {"active": int(active or 0), "queued": int(queued or 0), "pending_chat": int(pending_chat)}
