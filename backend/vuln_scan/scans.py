"""Scan run log (#2572): one row per requested scan, in the findings database.

States are ``refused``, ``planned`` (dry run), ``running``, ``succeeded``,
``failed`` and ``aborted``. A ``running`` row holds the single fleet-wide slot.
Scans are dispatched from the hub only, so the hub's database is the slot's one
source of truth; a row left ``running`` by a crashed process is reclaimed after
:data:`STALE_RUNNING_HOURS`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from staff.store import _now

SCAN_STATES = ("refused", "planned", "running", "succeeded", "failed", "aborted")
STALE_RUNNING_HOURS = 14
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vs_scans (
    id            TEXT PRIMARY KEY,
    repo          TEXT NOT NULL,
    sha           TEXT NOT NULL,
    host          TEXT NOT NULL,
    dry_run       INTEGER NOT NULL,
    state         TEXT NOT NULL,
    phase         TEXT NOT NULL,
    reasons       TEXT NOT NULL,
    requested_by  TEXT NOT NULL,
    created       TEXT NOT NULL,
    finished      TEXT NOT NULL,
    cost_usd      REAL NOT NULL,
    turns         INTEGER NOT NULL,
    findings_new  INTEGER NOT NULL,
    findings_seen INTEGER NOT NULL,
    image_digest  TEXT NOT NULL,
    error         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vs_scans_state ON vs_scans(state, created);
"""
_FIELDS = {
    "state",
    "phase",
    "reasons",
    "finished",
    "cost_usd",
    "turns",
    "findings_new",
    "findings_seen",
    "image_digest",
    "error",
}


class SlotBusy(RuntimeError):
    """Another scan holds the fleet-wide slot."""


class ScanLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _reclaim_stale(self, conn: sqlite3.Connection) -> None:
        cutoff = (
            (datetime.now(UTC) - timedelta(hours=STALE_RUNNING_HOURS))
            .isoformat()
            .replace("+00:00", "Z")
        )
        conn.execute(
            "UPDATE vs_scans SET state='aborted', error='reclaimed: process died', "
            "finished=? WHERE state='running' AND created < ?",
            (_now(), cutoff),
        )

    def slot_free(self) -> bool:
        with self._lock, self._conn() as conn:
            self._reclaim_stale(conn)
            row = conn.execute(
                "SELECT COUNT(*) FROM vs_scans WHERE state='running'"
            ).fetchone()
            return int(row[0]) == 0

    def create(
        self,
        *,
        repo: str,
        sha: str,
        host: str,
        dry_run: bool,
        state: str,
        reasons: list[str],
        requested_by: str,
    ) -> str:
        """Insert a scan row; a ``running`` row atomically takes the slot."""
        if state not in SCAN_STATES:
            raise ValueError(f"bad scan state {state!r}")
        scan_id = uuid.uuid4().hex[:16]
        with self._lock, self._conn() as conn:
            if state == "running":
                self._reclaim_stale(conn)
                busy = conn.execute(
                    "SELECT COUNT(*) FROM vs_scans WHERE state='running'"
                ).fetchone()
                if int(busy[0]):
                    raise SlotBusy("the fleet-wide scan slot is busy")
            conn.execute(
                "INSERT INTO vs_scans VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    scan_id,
                    repo,
                    sha,
                    host,
                    int(dry_run),
                    state,
                    "admission",
                    json.dumps(reasons),
                    requested_by,
                    _now(),
                    "" if state == "running" else _now(),
                    0.0,
                    0,
                    0,
                    0,
                    "",
                    "",
                ),
            )
        return scan_id

    def update(self, scan_id: str, **fields: Any) -> None:
        unknown = set(fields) - _FIELDS
        if unknown:
            raise ValueError(f"unknown scan fields {sorted(unknown)}")
        if "reasons" in fields:
            fields["reasons"] = json.dumps(fields["reasons"])
        if fields.get("state") not in (None, *SCAN_STATES):
            raise ValueError(f"bad scan state {fields['state']!r}")
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock, self._conn() as conn:
            conn.execute(
                f"UPDATE vs_scans SET {cols} WHERE id=?", (*fields.values(), scan_id)
            )  # noqa: S608

    def get(self, scan_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM vs_scans WHERE id=?", (scan_id,)
            ).fetchone()
        return _row(row) if row else None

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM vs_scans ORDER BY created DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row(r) for r in rows]

    def spent_today(self) -> float:
        midnight = (
            datetime.now(UTC)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM vs_scans WHERE created >= ?",
                (midnight,),
            ).fetchone()
        return float(row[0])


def _row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["dry_run"] = bool(out["dry_run"])
    out["reasons"] = json.loads(out["reasons"] or "[]")
    return out


_log: ScanLog | None = None
_log_lock = threading.Lock()


def get_scan_log(path: Path) -> ScanLog:
    global _log  # noqa: PLW0603
    target = path.expanduser().resolve()
    with _log_lock:
        if _log is None or _log.path != target:
            _log = ScanLog(target)
        return _log
