from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from staff.store import _config_dir, _now, first_touch_lock

EscalationOutcome = Literal["applied", "stripped", "refused"]


@dataclass(frozen=True)
class EscalationEvent:
    repo: str
    pr_number: int
    github_actor: str
    outcome: EscalationOutcome
    reason: str = ""
    id: int | None = None
    ts: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "repo": self.repo,
            "pr_number": self.pr_number,
            "github_actor": self.github_actor,
            "outcome": self.outcome,
            "reason": self.reason,
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS escalation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    repo TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    github_actor TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS escalation_events_ts_idx ON escalation_events(ts);
CREATE INDEX IF NOT EXISTS escalation_events_repo_pr_idx ON escalation_events(repo, pr_number);
"""


def default_db_path() -> Path:
    return Path(os.environ.get("ESCALATION_EVENTS_DB", str(_config_dir() / "escalation_events.sqlite3"))).expanduser()


class EscalationStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with first_touch_lock(self.path), self._lock:
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=30.0)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA busy_timeout = 30000")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)

    def append(self, event: EscalationEvent) -> int:
        assert event.repo.strip(), "repo required"  # noqa: S101
        assert event.pr_number > 0, "pr_number required"  # noqa: S101
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO escalation_events "
                "(ts, repo, pr_number, github_actor, outcome, reason) VALUES (?, ?, ?, ?, ?, ?)",
                (event.ts, event.repo, event.pr_number, event.github_actor, event.outcome, event.reason),
            )
            row_id = cur.lastrowid
            if row_id is None:
                raise RuntimeError("escalation_events INSERT did not return row id")
            return int(row_id)

    def list_recent(self, *, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM escalation_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_active(self) -> list[dict[str, Any]]:
        latest: dict[tuple[str, int], dict[str, Any]] = {}
        for row in self.list_recent(limit=200):
            key = (row["repo"], row["pr_number"])
            if key not in latest:
                latest[key] = row
        return [r for r in latest.values() if r["outcome"] == "applied"]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        return {
            "id": d["id"],
            "ts": d["ts"],
            "repo": d["repo"],
            "pr_number": d["pr_number"],
            "github_actor": d["github_actor"],
            "outcome": d["outcome"],
            "reason": d["reason"],
        }


_store: EscalationStore | None = None


def get_escalation_store() -> EscalationStore:
    global _store
    if _store is None:
        _store = EscalationStore()
    return _store


def reset_escalation_store() -> None:
    global _store
    _store = None
