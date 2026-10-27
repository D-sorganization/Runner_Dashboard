"""SQLite persistence for billing-actions usage snapshots (#2878, slice 1)."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

from billing_actions.models import UsageItem

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS billing_actions_usage (
    usage_date      TEXT NOT NULL,
    repo_full       TEXT NOT NULL,
    sku             TEXT NOT NULL,
    is_public       INTEGER NOT NULL,
    minutes_used    REAL NOT NULL,
    synced_at       TEXT NOT NULL,
    PRIMARY KEY (usage_date, repo_full, sku)
);
"""


def _db_path() -> Path:
    return Path(
        os.environ.get(
            "BILLING_ACTIONS_DB_PATH",
            str(Path.home() / "actions-runners" / "dashboard" / "billing_actions.db"),
        )
    )


def init_db(path: Path | None = None) -> Path:
    """Ensure schema exists; return resolved path."""
    db = path or _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db), timeout=5.0) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(SCHEMA_SQL)
    return db


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def upsert_usage_item(conn: sqlite3.Connection, item: UsageItem, *, synced_at: datetime) -> bool:
    """Insert or replace one usage row, keyed (date, repo, sku).

    Post: a second upsert of an unchanged item leaves the row count the same
    (idempotent re-sync of the trailing days never duplicates rows).
    """
    row = conn.execute(
        "SELECT minutes_used FROM billing_actions_usage WHERE usage_date=? AND repo_full=? AND sku=?",
        (item.date.isoformat(), item.repo_full, item.sku),
    ).fetchone()
    changed = row is None or row[0] != item.minutes_used
    conn.execute(
        """
        INSERT INTO billing_actions_usage (
            usage_date, repo_full, sku, is_public, minutes_used, synced_at
        ) VALUES (?,?,?,?,?,?)
        ON CONFLICT(usage_date, repo_full, sku) DO UPDATE SET
            is_public=excluded.is_public,
            minutes_used=excluded.minutes_used,
            synced_at=excluded.synced_at
        """,
        (
            item.date.isoformat(),
            item.repo_full,
            item.sku,
            int(item.is_public),
            item.minutes_used,
            synced_at.astimezone(UTC).isoformat(),
        ),
    )
    return changed


def load_usage_since(conn: sqlite3.Connection, *, since: date) -> list[UsageItem]:
    """Return stored usage rows on or after ``since``, oldest first."""
    rows = conn.execute(
        "SELECT usage_date, repo_full, sku, is_public, minutes_used "
        "FROM billing_actions_usage WHERE usage_date >= ? ORDER BY usage_date",
        (since.isoformat(),),
    ).fetchall()
    return [
        UsageItem(
            date=date.fromisoformat(row[0]),
            repo_full=row[1],
            sku=row[2],
            is_public=bool(row[3]),
            minutes_used=row[4],
        )
        for row in rows
    ]
