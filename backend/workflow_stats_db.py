"""Additive SQLite schema and connection boundary for workflow statistics."""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id            INTEGER PRIMARY KEY,
    repo              TEXT    NOT NULL,
    workflow_name     TEXT    NOT NULL,
    workflow_id       INTEGER NOT NULL,
    head_branch       TEXT,
    event             TEXT,
    status            TEXT,
    conclusion        TEXT,
    created_at        TEXT    NOT NULL,
    run_started_at    TEXT,
    updated_at        TEXT,
    queued_seconds    REAL,
    duration_seconds  REAL,
    runner_label      TEXT,
    inserted_at       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wfr_workflow ON workflow_runs(repo, workflow_name, created_at);
CREATE INDEX IF NOT EXISTS idx_wfr_created  ON workflow_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_wfr_conclusion ON workflow_runs(conclusion);

CREATE TABLE IF NOT EXISTS collection_state (
    key       TEXT PRIMARY KEY,
    value     TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_jobs (
    repo               TEXT NOT NULL,
    job_id             INTEGER NOT NULL,
    run_id             INTEGER NOT NULL,
    run_attempt        INTEGER,
    event              TEXT,
    workflow_name      TEXT,
    job_name           TEXT NOT NULL,
    status             TEXT,
    conclusion         TEXT,
    created_at         TEXT,
    started_at         TEXT,
    completed_at       TEXT,
    queue_seconds      REAL,
    execute_seconds    REAL,
    runner_id          INTEGER,
    runner_name        TEXT,
    runner_group_id    INTEGER,
    runner_group_name  TEXT,
    labels_json        TEXT NOT NULL DEFAULT '[]',
    observed_at        TEXT NOT NULL,
    PRIMARY KEY (repo, job_id)
);
CREATE INDEX IF NOT EXISTS idx_workflow_jobs_created ON workflow_jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_workflow_jobs_event_created ON workflow_jobs(event, created_at);
CREATE INDEX IF NOT EXISTS idx_workflow_jobs_run ON workflow_jobs(repo, run_id, run_attempt);

CREATE TABLE IF NOT EXISTS workflow_job_runs (
    org                TEXT NOT NULL,
    repo               TEXT NOT NULL,
    run_id             INTEGER NOT NULL,
    latest_attempt     INTEGER,
    run_updated_at     TEXT,
    run_status         TEXT,
    run_conclusion     TEXT,
    collection_reasons_json TEXT NOT NULL DEFAULT '[]',
    collection_complete INTEGER NOT NULL DEFAULT 0,
    observed_at        TEXT NOT NULL,
    PRIMARY KEY (org, repo, run_id)
);
CREATE INDEX IF NOT EXISTS idx_workflow_job_runs_completion
    ON workflow_job_runs(org, repo, collection_complete, run_updated_at);
"""


def _db_path() -> Path:
    """Resolve the database file location (env-overridable)."""
    return Path(
        os.environ.get(
            "STATS_DB_PATH",
            str(Path.home() / "actions-runners" / "dashboard" / "stats.db"),
        )
    )


def init_db(path: Path | None = None) -> Path:
    """Create the DB file + schema if missing; return resolved path."""
    path = path or _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=5.0) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(SCHEMA_SQL)
    log.info("workflow_stats: DB initialized at %s", path)
    return path


@contextmanager
def connect_stats_db(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open the configured workflow-stats database with its additive schema.

    This is the public connection boundary for adjacent workflow-stat stores;
    callers do not need to reach into this module's private path/connect helpers.
    """
    resolved = init_db(path)
    conn = sqlite3.connect(resolved, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def open_stats_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn
