"""SQLite reservations and reconciled armed-PR inventory (RD-CI-005)."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import timedelta
from pathlib import Path

from dispatch_arm_cap.time_parse import parse_iso_timestamp
from dispatch_arm_cap.types import AdmitContext, AdmitDecision, HealthVerdict
from time_utils import utc_now_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS arm_reservations (
    token TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    pr INTEGER NOT NULL,
    status TEXT NOT NULL,
    tier TEXT NOT NULL,
    dispatch_kind TEXT NOT NULL,
    reserved_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    confirmed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_arm_res_repo_pr_pending
    ON arm_reservations(repo, pr) WHERE status = 'pending';
CREATE TABLE IF NOT EXISTS armed_inventory (
    repo TEXT NOT NULL,
    pr INTEGER NOT NULL,
    healthy INTEGER NOT NULL,
    lifecycle TEXT NOT NULL,
    reasons TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (repo, pr)
);
CREATE TABLE IF NOT EXISTS cap_disarm_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class ArmCapStore:
    """SQLite-backed reservations and reconciled armed inventory."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def expire_stale_reservations(self, now_iso: str | None = None) -> int:
        now = now_iso or utc_now_iso()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE arm_reservations SET status = 'expired' WHERE status = 'pending' AND expires_at <= ?",
                (now,),
            )
            return int(cur.rowcount)

    def count_healthy_armed(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM armed_inventory WHERE healthy = 1").fetchone()
            return int(row["c"] if row else 0)

    def count_pending_reservations(self, exclude_token: str | None = None) -> int:
        with self._lock, self._connect() as conn:
            if exclude_token:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM arm_reservations WHERE status = 'pending' AND token != ?",
                    (exclude_token,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS c FROM arm_reservations WHERE status = 'pending'").fetchone()
            return int(row["c"] if row else 0)

    def effective_healthy_load(self, exclude_token: str | None = None) -> int:
        return self.count_healthy_armed() + self.count_pending_reservations(exclude_token)

    def upsert_inventory(
        self,
        repo: str,
        pr: int,
        verdict: HealthVerdict,
        *,
        updated_at: str | None = None,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO armed_inventory (repo, pr, healthy, lifecycle, reasons, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(repo, pr) DO UPDATE SET "
                "healthy=excluded.healthy, lifecycle=excluded.lifecycle, "
                "reasons=excluded.reasons, updated_at=excluded.updated_at",
                (
                    repo,
                    pr,
                    1 if verdict.healthy else 0,
                    verdict.lifecycle,
                    json.dumps(list(verdict.reasons)),
                    updated_at or utc_now_iso(),
                ),
            )

    def remove_inventory(self, repo: str, pr: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM armed_inventory WHERE repo = ? AND pr = ?", (repo, pr))

    def try_reserve(
        self,
        repo: str,
        pr: int,
        ctx: AdmitContext,
        *,
        cap: int,
        ttl_seconds: int,
        now_iso: str | None = None,
    ) -> AdmitDecision:
        """Atomically reserve a cap slot if load < cap (single-flight per repo#pr)."""
        now = now_iso or utc_now_iso()
        expires = (parse_iso_timestamp(now) + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z")
        token = str(uuid.uuid4())
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._expire_in_conn(conn, now)
            pending_same = conn.execute(
                "SELECT token FROM arm_reservations WHERE repo = ? AND pr = ? AND status = 'pending'",
                (repo, pr),
            ).fetchone()
            if pending_same:
                return AdmitDecision(
                    allowed=True,
                    reservation_token=str(pending_same["token"]),
                    reason="existing pending reservation",
                )
            load = self._effective_load_in_conn(conn)
            if load >= cap:
                return AdmitDecision(
                    allowed=False,
                    reason=f"healthy armed cap reached ({load}/{cap})",
                    healthy_armed=load,
                    cap=cap,
                )
            try:
                conn.execute(
                    "INSERT INTO arm_reservations "
                    "(token, repo, pr, status, tier, dispatch_kind, reserved_at, expires_at) "
                    "VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)",
                    (token, repo, pr, ctx.tier, ctx.dispatch_kind, now, expires),
                )
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                return AdmitDecision(allowed=False, reason="reservation race lost")
            conn.execute("COMMIT")
            return AdmitDecision(allowed=True, reservation_token=token, healthy_armed=load + 1, cap=cap)

    def confirm_reservation(self, token: str, *, now_iso: str | None = None) -> bool:
        now = now_iso or utc_now_iso()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE arm_reservations SET status = 'confirmed', confirmed_at = ? "
                "WHERE token = ? AND status = 'pending'",
                (now, token),
            )
            return cur.rowcount == 1

    def release_reservation(self, token: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE arm_reservations SET status = 'released' WHERE token = ? AND status = 'pending'",
                (token,),
            )

    def recover_pending_as_inventory_gap(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM arm_reservations WHERE status = 'pending'").fetchone()
            return int(row["c"] if row else 0)

    def get_disarm_state(self, key: str) -> str:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM cap_disarm_state WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else ""

    def set_disarm_state(self, key: str, value: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO cap_disarm_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    @staticmethod
    def _expire_in_conn(conn: sqlite3.Connection, now_iso: str) -> None:
        conn.execute(
            "UPDATE arm_reservations SET status = 'expired' WHERE status = 'pending' AND expires_at <= ?",
            (now_iso,),
        )

    @staticmethod
    def _effective_load_in_conn(conn: sqlite3.Connection) -> int:
        inv = conn.execute("SELECT COUNT(*) AS c FROM armed_inventory WHERE healthy = 1").fetchone()
        pending = conn.execute("SELECT COUNT(*) AS c FROM arm_reservations WHERE status = 'pending'").fetchone()
        return int(inv["c"] if inv else 0) + int(pending["c"] if pending else 0)
