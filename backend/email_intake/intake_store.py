"""SQLite persistence for intake audit, replay protection, and rate limits."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from staff.store import _config_dir, first_touch_lock

_SCHEMA = """
CREATE TABLE IF NOT EXISTS intake_messages (
  message_id TEXT PRIMARY KEY,
  from_address TEXT NOT NULL,
  disposition TEXT NOT NULL,
  received_at REAL NOT NULL,
  recorded_at REAL NOT NULL,
  detail_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intake_messages_from_time
  ON intake_messages(from_address, recorded_at);
"""


@dataclass
class IntakeStore:
    path: Path

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with first_touch_lock(self.path):
            conn = sqlite3.connect(self.path)
            conn.executescript(_SCHEMA)
            return conn

    def has_message_id(self, message_id: str) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT 1 FROM intake_messages WHERE message_id = ?", (message_id,)).fetchone()
        return row is not None

    def count_sender_last_hour(self, from_address: str, *, now: float | None = None) -> int:
        moment = now or time.time()
        since = moment - 3600.0
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM intake_messages WHERE from_address = ? AND recorded_at >= ?",
                (from_address.lower(), since),
            ).fetchone()
        return int(row[0]) if row else 0

    def record(
        self,
        *,
        message_id: str,
        from_address: str,
        disposition: str,
        received_at: float,
        detail: dict[str, Any],
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO intake_messages
                (message_id, from_address, disposition, received_at, recorded_at, detail_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    from_address.lower(),
                    disposition,
                    received_at,
                    time.time(),
                    json.dumps(detail, sort_keys=True),
                ),
            )
            conn.commit()

    def quarantine_count(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM intake_messages WHERE disposition = 'quarantine'").fetchone()
        return int(row[0]) if row else 0


def default_intake_store() -> IntakeStore:
    return IntakeStore(_config_dir() / "email_intake.sqlite3")
