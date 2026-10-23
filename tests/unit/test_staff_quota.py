"""Unit tests for expiring stale blocking quota snapshots (#2478)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from staff import quota

NOW = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)


def test_stale_blocking_window_expires_after_two_hours_to_prevent_deadlock(tmp_path: Path) -> None:
    store = quota.QuotaStore(tmp_path / "staff_quota.json")
    stale_observed = NOW - timedelta(hours=2, minutes=5)
    snap = quota.QuotaSnapshot(
        account="claude",
        observed_at=stale_observed,
        source="claude-stream",
        windows=(
            quota.QuotaWindow("five_hour", 1.0, NOW + timedelta(hours=2)),
            quota.QuotaWindow("seven_day", 96.0, NOW + timedelta(days=2)),
        ),
    )
    store.record(snap)

    ok_recent, _ = quota.headroom("claude", 85.0, stale_observed + timedelta(minutes=30), store=store)
    ok_stale, reason_stale = quota.headroom("claude", 85.0, NOW, store=store)

    assert not ok_recent
    assert ok_stale, reason_stale
