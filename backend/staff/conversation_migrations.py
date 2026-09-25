"""Database migrations and backup runner for staff conversations (SC-B2, Issue #1305)."""

from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from pathlib import Path

from staff.conversation_models import ConversationStoreStatus, _now

log = logging.getLogger("dashboard.staff.conversations.migrations")

_MIGRATION_1_SQL = """
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    participants TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    last_message_at TEXT,
    unread_counters TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS threads_status_idx ON threads(status);
CREATE INDEX IF NOT EXISTS threads_updated_idx ON threads(updated_at);
CREATE INDEX IF NOT EXISTS threads_kind_idx ON threads(kind);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(id),
    seq INTEGER NOT NULL,
    author_kind TEXT NOT NULL,
    author TEXT NOT NULL,
    kind TEXT NOT NULL,
    body_md TEXT NOT NULL DEFAULT '',
    meta TEXT NOT NULL DEFAULT '{}',
    run_id TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT,
    created_at TEXT NOT NULL,
    delivery TEXT NOT NULL DEFAULT 'complete'
);
CREATE UNIQUE INDEX IF NOT EXISTS messages_thread_seq_idx ON messages(thread_id, seq);
CREATE UNIQUE INDEX IF NOT EXISTS messages_thread_idempotency_idx
    ON messages(thread_id, idempotency_key) WHERE idempotency_key IS NOT NULL AND idempotency_key != '';
CREATE INDEX IF NOT EXISTS messages_run_idx ON messages(run_id);
CREATE INDEX IF NOT EXISTS messages_created_idx ON messages(created_at);

CREATE TABLE IF NOT EXISTS action_proposals (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(id),
    thread_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    risk TEXT NOT NULL DEFAULT 'low',
    state TEXT NOT NULL DEFAULT 'proposed',
    decided_by TEXT NOT NULL DEFAULT '',
    decided_at TEXT,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS action_proposals_thread_idx ON action_proposals(thread_id);
CREATE INDEX IF NOT EXISTS action_proposals_message_idx ON action_proposals(message_id);
CREATE INDEX IF NOT EXISTS action_proposals_state_idx ON action_proposals(state);
"""

CORE_MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "conversations_core_tables", _MIGRATION_1_SQL),
]


def backup_database(db_path: Path) -> Path | None:
    """Create a timestamped backup before running schema migrations."""
    if not db_path.exists() or db_path.stat().st_size == 0:
        return None
    now_ts = int(time.time())
    counter = 0
    backup_path = db_path.parent / f"{db_path.name}.bak.{now_ts}"
    while backup_path.exists():
        counter += 1
        backup_path = db_path.parent / f"{db_path.name}.bak.{now_ts}_{counter}"
    try:
        shutil.copy2(db_path, backup_path)
        log.info("Backed up database to %s before migration", backup_path)
        return backup_path
    except Exception as exc:
        log.warning("Failed to create database backup before migration: %s", exc)
        return None


def run_migrations(
    conn: sqlite3.Connection,
    db_path: Path,
    migrations: list[tuple[int, str, str]] | None = None,
) -> ConversationStoreStatus:
    """Execute forward-only migrations and record in schema_migrations."""
    to_apply = migrations if migrations is not None else CORE_MIGRATIONS
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL, "
            "applied_at TEXT NOT NULL"
            ")"
        )
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        applied_versions = {int(r["version"]) for r in rows}
        max_version = max(applied_versions) if applied_versions else 0

        pending = [m for m in to_apply if m[0] not in applied_versions]
        if not pending:
            return ConversationStoreStatus(available=True, migration_version=max_version)

        backup_database(db_path)

        for version, name, sql in pending:
            log.info("Applying migration %d: %s", version, name)
            now_str = _now()
            script = (
                f"{sql}\n"
                f"INSERT INTO schema_migrations (version, name, applied_at) "
                f"VALUES ({version}, '{name}', '{now_str}');\n"
            )
            conn.executescript(script)
            max_version = max(max_version, version)

        return ConversationStoreStatus(available=True, migration_version=max_version)
    except Exception as exc:
        log.error("Conversation schema migration failed (failing safe): %s", exc)
        return ConversationStoreStatus(
            available=False,
            error=str(exc),
            banner_message=f"Conversations disabled: schema migration failed: {exc}",
        )
