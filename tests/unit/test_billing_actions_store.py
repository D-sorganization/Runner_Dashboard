"""Store tests for the Actions-minutes billing monitor (#2878, slice 1)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

from billing_actions.models import UsageItem
from billing_actions.store import init_db, load_usage_since, upsert_usage_item


def _conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = init_db(tmp_path / "billing_actions.db")
    return sqlite3.connect(str(db_path))


def _item(minutes: float = 10.0) -> UsageItem:
    return UsageItem(
        date=date(2026, 10, 1),
        repo_full="org/private",
        sku="UBUNTU",
        is_public=False,
        minutes_used=minutes,
    )


def test_poller_upsert_idempotent_no_duplicate_rows(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    synced_at = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

    first = upsert_usage_item(conn, _item(), synced_at=synced_at)
    second = upsert_usage_item(conn, _item(), synced_at=synced_at)
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM billing_actions_usage").fetchone()[0]
    assert count == 1
    assert first is True
    assert second is False


def test_upsert_changed_value_is_reported_and_replaces_row(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    synced_at = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

    upsert_usage_item(conn, _item(minutes=10.0), synced_at=synced_at)
    changed = upsert_usage_item(conn, _item(minutes=25.0), synced_at=synced_at)
    conn.commit()

    rows = load_usage_since(conn, since=date(2026, 10, 1))
    assert changed is True
    assert len(rows) == 1
    assert rows[0].minutes_used == 25.0


def test_load_usage_since_excludes_earlier_dates(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    synced_at = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    old_item = UsageItem(
        date=date(2026, 9, 1),
        repo_full="org/private",
        sku="UBUNTU",
        is_public=False,
        minutes_used=5.0,
    )
    upsert_usage_item(conn, old_item, synced_at=synced_at)
    upsert_usage_item(conn, _item(), synced_at=synced_at)
    conn.commit()

    rows = load_usage_since(conn, since=date(2026, 10, 1))
    assert len(rows) == 1
    assert rows[0].date == date(2026, 10, 1)
