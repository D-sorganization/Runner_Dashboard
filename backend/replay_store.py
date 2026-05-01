"""Bounded replay-protection store backed by SQLite (issue #344).

Replaces the unbounded JSON file that grew forever with a SQLite table that:
- Caps at ``max_entries`` (oldest evicted when exceeded).
- Purges expired entries on every write and periodically via background task.
- Provides O(1) average lookup via the primary-key index.
- Uses ``isolation_level=None`` (autocommit) for simplicity; all calls are
  serialised through the single asyncio lock in ``server.py``.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger("dashboard.replay_store")

_DEFAULT_TTL_S: int = 86_400  # 24 h
_DEFAULT_MAX_ENTRIES: int = 100_000


class ReplayStore:
    """SQLite-backed envelope deduplication store with TTL and size cap."""

    def __init__(
        self,
        path: Path,
        ttl_s: int = _DEFAULT_TTL_S,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._path = path
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db: sqlite3.Connection = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS processed (  id TEXT PRIMARY KEY,  expires_at REAL NOT NULL)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_expires ON processed (expires_at)")

    def is_replay(self, envelope_id: str) -> bool:
        """Return True if *envelope_id* has been recorded and has not expired."""
        now = time.time()
        row = self._db.execute("SELECT expires_at FROM processed WHERE id = ?", (envelope_id,)).fetchone()
        return row is not None and row[0] > now

    def record(self, envelope_id: str) -> None:
        """Record *envelope_id* as processed; evicts oldest entries if over the cap."""
        expires_at = time.time() + self._ttl_s
        self._db.execute(
            "INSERT INTO processed (id, expires_at) VALUES (?, ?)"
            "  ON CONFLICT(id) DO UPDATE SET expires_at = excluded.expires_at",
            (envelope_id, expires_at),
        )
        self._evict_if_needed()

    def purge_expired(self) -> int:
        """Delete all rows whose TTL has elapsed.  Returns the count deleted."""
        now = time.time()
        cur = self._db.execute("DELETE FROM processed WHERE expires_at <= ?", (now,))
        deleted: int = cur.rowcount
        if deleted:
            log.debug("replay_store: purged %d expired entries", deleted)
        return deleted

    def _evict_if_needed(self) -> None:
        """Evict the oldest entries when the table exceeds *max_entries*."""
        (count,) = self._db.execute("SELECT COUNT(*) FROM processed").fetchone()
        if count <= self._max_entries:
            return
        excess = count - self._max_entries
        self._db.execute(
            "DELETE FROM processed WHERE id IN (  SELECT id FROM processed ORDER BY expires_at ASC LIMIT ?)",
            (excess,),
        )
        log.debug("replay_store: evicted %d oldest entries (cap=%d)", excess, self._max_entries)

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        try:
            self._db.close()
        except Exception:  # noqa: BLE001
            pass


def migrate_json_to_sqlite(json_path: Path, store: ReplayStore) -> int:
    """One-shot migration: import a legacy JSON envelope file into *store*.

    Returns the number of entries imported.  No-ops silently if the JSON file
    does not exist or cannot be parsed.
    """
    import json  # noqa: PLC0415

    if not json_path.exists():
        return 0
    try:
        data: dict = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        log.warning("replay_store: could not read legacy JSON at %s: %s", json_path, exc)
        return 0

    now = time.time()
    count = 0
    for eid, expires_at in data.items():
        try:
            exp = float(expires_at)
        except (TypeError, ValueError):
            continue
        if exp > now:  # only import non-expired entries
            store._db.execute(  # noqa: SLF001
                "INSERT OR IGNORE INTO processed (id, expires_at) VALUES (?, ?)", (eid, exp)
            )
            count += 1

    log.info("replay_store: imported %d entries from legacy JSON %s", count, json_path)
    return count
