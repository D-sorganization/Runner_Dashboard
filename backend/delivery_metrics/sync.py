"""Incremental GitHub → SQLite sync for delivery metrics (#2119)."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from delivery_metrics.config import delivery_metrics_enabled
from delivery_metrics.db import (
    get_repo_sync_cursor,
    init_db,
    set_repo_sync_cursor,
    transaction,
    upsert_check_run,
    upsert_pull,
)
from delivery_metrics.models import CheckRunSnapshot, PrSnapshot
from delivery_metrics.ports import GitHubDeliveryCollectorPort
from gh_client import GhRateLimited

log = logging.getLogger("dashboard.delivery_metrics.sync")

_DEFAULT_LOOKBACK = timedelta(days=30)
_POLL_INTERVAL_S = float(os.environ.get("DELIVERY_METRICS_SYNC_INTERVAL_S", "900"))


@dataclass(frozen=True)
class SyncStats:
    """Observability for one collect pass."""

    repos: int
    pulls_upserted: int
    check_runs_upserted: int
    rate_limited: bool

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "repos": self.repos,
            "pulls_upserted": self.pulls_upserted,
            "check_runs_upserted": self.check_runs_upserted,
            "rate_limited": self.rate_limited,
        }


async def _fetch_repo(
    collector: GitHubDeliveryCollectorPort,
    conn: sqlite3.Connection,
    repo_full: str,
    *,
    now: datetime,
) -> tuple[list[tuple[PrSnapshot, tuple[CheckRunSnapshot, ...]]], datetime | None, bool]:
    since = get_repo_sync_cursor(conn, repo_full)
    if since is None:
        since = now - _DEFAULT_LOOKBACK
    try:
        pulls = await collector.list_pulls_updated_since(repo_full, since)
    except GhRateLimited:
        return ([], None, True)
    bundles: list[tuple[PrSnapshot, tuple[CheckRunSnapshot, ...]]] = []
    max_updated = since
    for pr in pulls:
        if pr.updated_at > max_updated:
            max_updated = pr.updated_at
        if not pr.head_sha:
            bundles.append((pr, ()))
            continue
        try:
            runs = await collector.list_check_runs(repo_full, pr.head_sha)
        except GhRateLimited:
            return (bundles, max_updated if bundles else None, True)
        bundles.append((pr, runs))
    return (bundles, max_updated if bundles else None, False)


def _persist_repo(
    conn: sqlite3.Connection,
    repo_full: str,
    bundles: list[tuple[PrSnapshot, tuple[CheckRunSnapshot, ...]]],
    *,
    max_updated: datetime,
    now: datetime,
) -> tuple[int, int]:
    pulls_n = 0
    checks_n = 0
    with transaction(conn):
        for pr, runs in bundles:
            changed = upsert_pull(conn, pr, synced_at=now)
            if not changed:
                continue
            pulls_n += 1
            for run in runs:
                upsert_check_run(conn, run, synced_at=now)
                checks_n += 1
        if bundles:
            set_repo_sync_cursor(conn, repo_full, last_pull_updated_at=max_updated, synced_at=now)
    return pulls_n, checks_n


async def sync_repo(
    collector: GitHubDeliveryCollectorPort,
    conn: sqlite3.Connection,
    repo_full: str,
    *,
    now: datetime | None = None,
) -> tuple[int, int, bool]:
    """Sync one repo. Returns (pulls_changed, check_runs_written, rate_limited)."""
    moment = now or datetime.now(UTC)
    bundles, max_updated, limited = await _fetch_repo(collector, conn, repo_full, now=moment)
    if limited:
        return (0, 0, True)
    if not bundles or max_updated is None:
        return (0, 0, False)
    p, c = _persist_repo(conn, repo_full, bundles, max_updated=max_updated, now=moment)
    return (p, c, False)


async def collect_delivery_metrics_once(
    collector: GitHubDeliveryCollectorPort,
    repos: list[str],
    *,
    db_path: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> SyncStats:
    """Idempotent incremental sync for ``repos`` (full ``owner/name`` slugs)."""
    if not repos:
        return SyncStats(0, 0, 0, False)
    path = init_db(db_path)
    owns_conn = conn is None
    if conn is None:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
    pulls_total = 0
    checks_total = 0
    rate_limited = False
    try:
        for repo in repos:
            p, c, limited = await sync_repo(collector, conn, repo)
            pulls_total += p
            checks_total += c
            if limited:
                rate_limited = True
                break
    finally:
        if owns_conn:
            conn.close()
    stats = SyncStats(len(repos), pulls_total, checks_total, rate_limited)
    log.info("delivery_metrics sync: %s", stats.to_dict())
    return stats


async def delivery_metrics_sync_loop(
    collector_factory: Callable[[], GitHubDeliveryCollectorPort],
    repos: list[str],
) -> None:
    """Background loop; no-op when the feature flag is off."""
    while True:
        if not delivery_metrics_enabled():
            await asyncio.sleep(_POLL_INTERVAL_S)
            continue
        try:
            await collect_delivery_metrics_once(collector_factory(), repos)
        except Exception as exc:  # noqa: BLE001
            log.warning("delivery_metrics sync tick failed: %s", exc)
        await asyncio.sleep(_POLL_INTERVAL_S)


def start_delivery_metrics_sync_loop(
    collector_factory: Callable[[], GitHubDeliveryCollectorPort],
    repos: list[str],
) -> None:
    """Schedule the sync loop (server startup)."""
    asyncio.create_task(delivery_metrics_sync_loop(collector_factory, repos))
