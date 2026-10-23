"""SQLite append-only store for assistant memory entries."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memory.contract import MAX_STORE_BYTES
from memory.models import MemoryEntry

_STORE: AssistantMemoryStore | None = None


def default_db_path() -> Path:
    configured = os.environ.get("RUNNER_DASHBOARD_CONFIG_DIR")
    base = Path(configured).expanduser() if configured else Path("~/.config/runner-dashboard").expanduser()
    return Path(os.environ.get("ASSISTANT_MEMORY_DB", str(base / "assistant_memory.sqlite3"))).expanduser()


@dataclass
class _Row:
    id: str
    entry_type: str
    author_principal: str
    source_product: str
    tags_json: str
    body_json: str
    created_at: str
    expires_at: str | None
    supersedes_id: str | None
    superseded_by: str | None

    def to_entry(self) -> MemoryEntry:
        return MemoryEntry(
            id=self.id,
            entry_type=self.entry_type,  # type: ignore[arg-type]
            author_principal=self.author_principal,
            source_product=self.source_product,
            tags=json.loads(self.tags_json) if self.tags_json else [],
            body=json.loads(self.body_json),
            created_at=self.created_at,
            expires_at=self.expires_at,
            supersedes_id=self.supersedes_id,
            superseded_by=self.superseded_by,
        )


class AssistantMemoryStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=30000")
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_entries (
              id TEXT PRIMARY KEY,
              entry_type TEXT NOT NULL,
              author_principal TEXT NOT NULL,
              source_product TEXT NOT NULL,
              tags_json TEXT NOT NULL,
              body_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              expires_at TEXT,
              supersedes_id TEXT,
              superseded_by TEXT
            )
            """
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS memory_created_idx ON memory_entries(created_at)")
        self._db.execute("CREATE INDEX IF NOT EXISTS memory_type_idx ON memory_entries(entry_type)")
        self._db.execute("CREATE INDEX IF NOT EXISTS memory_author_idx ON memory_entries(author_principal)")
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_idempotency (
              idempotency_key TEXT PRIMARY KEY,
              fingerprint TEXT NOT NULL,
              entry_id TEXT NOT NULL UNIQUE
            )
            """
        )

    def total_payload_bytes(self) -> int:
        row = self._db.execute(
            "SELECT COALESCE(SUM(LENGTH(CAST(body_json AS BLOB))), 0) FROM memory_entries"
        ).fetchone()
        return int(row[0] if row else 0)

    def append(self, entry: MemoryEntry) -> MemoryEntry:
        payload = len(json.dumps(entry.body, separators=(",", ":")).encode("utf-8"))
        if self.total_payload_bytes() + payload > MAX_STORE_BYTES:
            raise ValueError("store_size_cap_exceeded")
        with self._lock:
            self._db.execute(
                """
                INSERT INTO memory_entries (
                  id, entry_type, author_principal, source_product, tags_json, body_json,
                  created_at, expires_at, supersedes_id, superseded_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.entry_type,
                    entry.author_principal,
                    entry.source_product,
                    json.dumps(entry.tags),
                    json.dumps(entry.body),
                    entry.created_at,
                    entry.expires_at,
                    entry.supersedes_id,
                    entry.superseded_by,
                ),
            )
            if entry.supersedes_id:
                self._db.execute(
                    "UPDATE memory_entries SET superseded_by = ? WHERE id = ? AND superseded_by IS NULL",
                    (entry.id, entry.supersedes_id),
                )
        return entry

    def append_idempotent(
        self, entry: MemoryEntry, *, idempotency_key: str, fingerprint: str
    ) -> tuple[MemoryEntry, bool]:
        """Atomically append once for a stable key, or return the matching prior entry."""
        body_json = json.dumps(entry.body, separators=(",", ":"), ensure_ascii=False)
        payload = len(body_json.encode("utf-8"))
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                prior = self._db.execute(
                    "SELECT fingerprint, entry_id FROM memory_idempotency WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if prior:
                    if prior[0] != fingerprint:
                        raise ValueError("idempotency_key_reused")
                    row = self._db.execute("SELECT * FROM memory_entries WHERE id = ?", (prior[1],)).fetchone()
                    if row is None:
                        raise RuntimeError("memory_idempotency_entry_missing")
                    self._db.execute("COMMIT")
                    return _Row(*row).to_entry(), True

                if self.total_payload_bytes() + payload > MAX_STORE_BYTES:
                    raise ValueError("store_size_cap_exceeded")
                self._db.execute(
                    """
                    INSERT INTO memory_entries (
                      id, entry_type, author_principal, source_product, tags_json, body_json,
                      created_at, expires_at, supersedes_id, superseded_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.id,
                        entry.entry_type,
                        entry.author_principal,
                        entry.source_product,
                        json.dumps(entry.tags),
                        body_json,
                        entry.created_at,
                        entry.expires_at,
                        entry.supersedes_id,
                        entry.superseded_by,
                    ),
                )
                self._db.execute(
                    "INSERT INTO memory_idempotency (idempotency_key, fingerprint, entry_id) VALUES (?, ?, ?)",
                    (idempotency_key, fingerprint, entry.id),
                )
                self._db.execute("COMMIT")
                return entry, False
            except Exception:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def get(self, entry_id: str) -> MemoryEntry | None:
        row = self._db.execute("SELECT * FROM memory_entries WHERE id = ?", (entry_id,)).fetchone()
        if not row:
            return None
        return _Row(*row).to_entry()

    def list_entries(
        self,
        *,
        since: str | None = None,
        entry_type: str | None = None,
        tag: str | None = None,
        author: str | None = None,
        include_superseded: bool = False,
        limit: int = 100,
    ) -> list[MemoryEntry]:
        clauses: list[str] = []
        params: list[Any] = []
        if since:
            clauses.append("created_at >= ?")
            params.append(since)
        if entry_type:
            clauses.append("entry_type = ?")
            params.append(entry_type)
        if author:
            clauses.append("author_principal = ?")
            params.append(author)
        if not include_superseded:
            clauses.append("superseded_by IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM memory_entries {where} ORDER BY created_at DESC LIMIT ?"  # noqa: S608
        params.append(limit)
        rows = self._db.execute(sql, tuple(params)).fetchall()
        entries = [_Row(*row).to_entry() for row in rows]
        if tag:
            entries = [e for e in entries if tag in e.tags]
        return entries


def get_memory_store() -> AssistantMemoryStore:
    global _STORE
    if _STORE is None:
        _STORE = AssistantMemoryStore(default_db_path())
    return _STORE


def reset_memory_store() -> None:
    global _STORE
    _STORE = None


def new_entry_id() -> str:
    return f"mem_{uuid.uuid4().hex[:16]}"
