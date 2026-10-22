"""SQLite persistence for delivery metrics (#2119)."""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from delivery_metrics.models import CheckRunSnapshot, PrSnapshot

log = logging.getLogger("dashboard.delivery_metrics.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS dm_pulls (
    repo_full       TEXT NOT NULL,
    number          INTEGER NOT NULL,
    title           TEXT NOT NULL,
    state           TEXT NOT NULL,
    merged          INTEGER NOT NULL,
    draft           INTEGER NOT NULL,
    head_sha        TEXT NOT NULL,
    base_ref        TEXT NOT NULL,
    author_login    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    closed_at       TEXT,
    merged_at       TEXT,
    additions       INTEGER NOT NULL,
    deletions       INTEGER NOT NULL,
    changed_files   INTEGER NOT NULL,
    html_url        TEXT NOT NULL,
    synced_at       TEXT NOT NULL,
    PRIMARY KEY (repo_full, number)
);
CREATE INDEX IF NOT EXISTS idx_dm_pulls_updated ON dm_pulls(repo_full, updated_at);

CREATE TABLE IF NOT EXISTS dm_check_runs (
    repo_full       TEXT NOT NULL,
    check_run_id    INTEGER NOT NULL,
    head_sha        TEXT NOT NULL,
    name            TEXT NOT NULL,
    status          TEXT NOT NULL,
    conclusion      TEXT,
    started_at      TEXT,
    completed_at    TEXT,
    details_url     TEXT NOT NULL,
    app_slug        TEXT NOT NULL,
    synced_at       TEXT NOT NULL,
    PRIMARY KEY (repo_full, check_run_id)
);
CREATE INDEX IF NOT EXISTS idx_dm_checks_head ON dm_check_runs(repo_full, head_sha);

CREATE TABLE IF NOT EXISTS dm_repo_sync (
    repo_full              TEXT PRIMARY KEY,
    last_sync_at           TEXT NOT NULL,
    last_pull_updated_at   TEXT NOT NULL
);
"""


def _db_path() -> Path:
    return Path(
        os.environ.get(
            "DELIVERY_METRICS_DB_PATH",
            str(Path.home() / "actions-runners" / "dashboard" / "delivery_metrics.db"),
        )
    )


def init_db(path: Path | None = None) -> Path:
    """Ensure schema exists; return resolved path."""
    db = path or _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db), timeout=5.0) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(SCHEMA_SQL)
    log.info("delivery_metrics: DB initialized at %s", db)
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


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat().replace("+00:00", "Z")


def upsert_pull(conn: sqlite3.Connection, pr: PrSnapshot, *, synced_at: datetime) -> bool:
    """Insert or replace a pull row. Returns True when the row changed."""
    row = conn.execute(
        "SELECT head_sha, updated_at FROM dm_pulls WHERE repo_full=? AND number=?",
        (pr.repo_full, pr.number),
    ).fetchone()
    changed = row is None or row["head_sha"] != pr.head_sha or row["updated_at"] != _iso(pr.updated_at)
    conn.execute(
        """
        INSERT INTO dm_pulls (
            repo_full, number, title, state, merged, draft, head_sha, base_ref,
            author_login, created_at, updated_at, closed_at, merged_at,
            additions, deletions, changed_files, html_url, synced_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(repo_full, number) DO UPDATE SET
            title=excluded.title, state=excluded.state, merged=excluded.merged,
            draft=excluded.draft, head_sha=excluded.head_sha, base_ref=excluded.base_ref,
            author_login=excluded.author_login, created_at=excluded.created_at,
            updated_at=excluded.updated_at, closed_at=excluded.closed_at,
            merged_at=excluded.merged_at, additions=excluded.additions,
            deletions=excluded.deletions, changed_files=excluded.changed_files,
            html_url=excluded.html_url, synced_at=excluded.synced_at
        """,
        (
            pr.repo_full,
            pr.number,
            pr.title,
            pr.state,
            int(pr.merged),
            int(pr.draft),
            pr.head_sha,
            pr.base_ref,
            pr.author_login,
            _iso(pr.created_at),
            _iso(pr.updated_at),
            _iso(pr.closed_at),
            _iso(pr.merged_at),
            pr.additions,
            pr.deletions,
            pr.changed_files,
            pr.html_url,
            _iso(synced_at),
        ),
    )
    return changed


def upsert_check_run(conn: sqlite3.Connection, run: CheckRunSnapshot, *, synced_at: datetime) -> None:
    conn.execute(
        """
        INSERT INTO dm_check_runs (
            repo_full, check_run_id, head_sha, name, status, conclusion,
            started_at, completed_at, details_url, app_slug, synced_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(repo_full, check_run_id) DO UPDATE SET
            head_sha=excluded.head_sha, name=excluded.name, status=excluded.status,
            conclusion=excluded.conclusion, started_at=excluded.started_at,
            completed_at=excluded.completed_at, details_url=excluded.details_url,
            app_slug=excluded.app_slug, synced_at=excluded.synced_at
        """,
        (
            run.repo_full,
            run.check_run_id,
            run.head_sha,
            run.name,
            run.status,
            run.conclusion,
            _iso(run.started_at),
            _iso(run.completed_at),
            run.details_url,
            run.app_slug,
            _iso(synced_at),
        ),
    )


def get_repo_sync_cursor(conn: sqlite3.Connection, repo_full: str) -> datetime | None:
    row = conn.execute(
        "SELECT last_pull_updated_at FROM dm_repo_sync WHERE repo_full=?",
        (repo_full,),
    ).fetchone()
    if row is None:
        return None
    return datetime.fromisoformat(str(row["last_pull_updated_at"]).replace("Z", "+00:00"))


def set_repo_sync_cursor(
    conn: sqlite3.Connection,
    repo_full: str,
    *,
    last_pull_updated_at: datetime,
    synced_at: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO dm_repo_sync (repo_full, last_sync_at, last_pull_updated_at)
        VALUES (?,?,?)
        ON CONFLICT(repo_full) DO UPDATE SET
            last_sync_at=excluded.last_sync_at,
            last_pull_updated_at=excluded.last_pull_updated_at
        """,
        (repo_full, _iso(synced_at), _iso(last_pull_updated_at)),
    )
