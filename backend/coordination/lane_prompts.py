"""Durable SQLite WAL store for exclusive lane-prompt checkouts."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_DEFAULT_TTL_SEC = 3600
_BUSY_RETRIES = 8
_BUSY_SLEEP_S = 0.05


class LaneConflictError(RuntimeError):
    """Raised when a lane is already actively checked out or none are eligible."""


class StaleLaneFenceError(RuntimeError):
    """Raised when a heartbeat/release presents a stale fence or wrong session."""


def _default_db_path() -> Path:
    base = Path(
        os.environ.get(
            "RUNNER_DASHBOARD_CONFIG_DIR",
            str(Path("~/.config/runner-dashboard").expanduser()),
        )
    )
    return base / "lane_prompts.sqlite3"


class LanePromptStore:
    """Thread-safe, durable SQLite WAL store for lane-prompt checkouts."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else _default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _run_tx(self, fn: Any) -> Any:
        with self._lock:
            for attempt in range(_BUSY_RETRIES):
                conn = self._connect()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    result = fn(conn)
                    if conn.in_transaction:
                        conn.execute("COMMIT")
                    return result
                except sqlite3.OperationalError as exc:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    if "locked" in str(exc).lower() and attempt < _BUSY_RETRIES - 1:
                        time.sleep(_BUSY_SLEEP_S * (attempt + 1))
                        continue
                    raise
                except Exception:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
                finally:
                    conn.close()

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS lane_assignments (
                    lane_id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    repos_json TEXT NOT NULL,
                    release_sha TEXT NOT NULL,
                    package_hash TEXT NOT NULL,
                    subagent_model TEXT NOT NULL,
                    max_children INTEGER NOT NULL,
                    task_focus TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lane_checkouts (
                    checkout_id TEXT PRIMARY KEY,
                    lane_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    principal TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    fence_token INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    ttl_sec INTEGER NOT NULL,
                    expires_at REAL NOT NULL,
                    heartbeat_at REAL NOT NULL,
                    progress_at REAL,
                    release_sha TEXT NOT NULL,
                    package_hash TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_active_lane
                    ON lane_checkouts(lane_id) WHERE status = 'active';
                """
            )
        finally:
            conn.close()

    def register_assignment(
        self,
        *,
        lane_id: str,
        role: str,
        repos: list[str],
        release_sha: str,
        package_hash: str,
        subagent_model: str,
        max_children: int,
        task_focus: str = "",
        enabled: bool = True,
        now: float | None = None,
    ) -> dict[str, Any]:
        ts = time.time() if now is None else float(now)

        def _tx(conn: sqlite3.Connection) -> dict[str, Any]:
            conn.execute(
                """
                INSERT INTO lane_assignments (
                    lane_id, role, repos_json, release_sha, package_hash,
                    subagent_model, max_children, task_focus, enabled, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lane_id) DO UPDATE SET
                    role=excluded.role,
                    repos_json=excluded.repos_json,
                    release_sha=excluded.release_sha,
                    package_hash=excluded.package_hash,
                    subagent_model=excluded.subagent_model,
                    max_children=excluded.max_children,
                    task_focus=excluded.task_focus,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    lane_id,
                    role,
                    json.dumps(repos),
                    release_sha,
                    package_hash,
                    subagent_model,
                    int(max_children),
                    task_focus,
                    1 if enabled else 0,
                    ts,
                ),
            )
            return {"lane_id": lane_id, "role": role, "release_sha": release_sha}

        return self._run_tx(_tx)

    def list_catalog(self, now: float | None = None) -> list[dict[str, Any]]:
        ts = time.time() if now is None else float(now)

        def _tx(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            conn.execute(
                "UPDATE lane_checkouts SET status='expired' WHERE status='active' AND expires_at <= ?",
                (ts,),
            )
            rows = conn.execute(
                """
                SELECT a.lane_id, a.role, a.repos_json, a.release_sha, a.package_hash,
                       a.subagent_model, a.max_children, a.task_focus, a.enabled,
                       c.checkout_id, c.session_id, c.fence_token, c.expires_at
                FROM lane_assignments a
                LEFT JOIN lane_checkouts c
                  ON c.lane_id = a.lane_id AND c.status = 'active'
                ORDER BY a.lane_id
                """
            ).fetchall()
            return [
                {
                    "lane_id": r["lane_id"],
                    "role": r["role"],
                    "repos": json.loads(r["repos_json"]),
                    "release_sha": r["release_sha"],
                    "package_hash": r["package_hash"],
                    "subagent_model": r["subagent_model"],
                    "max_children": r["max_children"],
                    "task_focus": r["task_focus"],
                    "enabled": bool(r["enabled"]),
                    "active_checkout_id": r["checkout_id"],
                    "active_session_id": r["session_id"],
                }
                for r in rows
            ]

        return self._run_tx(_tx)

    def checkout(
        self,
        *,
        session_id: str,
        principal: str,
        idempotency_key: str,
        lane_id: str | None = None,
        role: str | None = None,
        ttl_sec: int = _DEFAULT_TTL_SEC,
        now: float | None = None,
    ) -> dict[str, Any]:
        ts = time.time() if now is None else float(now)
        ttl = max(60, int(ttl_sec))
        # Idempotency keys are caller supplied, so scope them to the session and
        # principal to prevent replaying another caller's receipt and fence.
        scoped_idempotency_key = json.dumps(
            [principal, session_id, idempotency_key], ensure_ascii=False, separators=(",", ":")
        )

        def _tx(conn: sqlite3.Connection) -> dict[str, Any]:
            existing_idem = conn.execute(
                """SELECT receipt_json FROM lane_checkouts
                   WHERE idempotency_key = ? AND principal = ? AND session_id = ?""",
                (scoped_idempotency_key, principal, session_id),
            ).fetchone()
            if existing_idem is not None:
                return json.loads(existing_idem["receipt_json"])

            # Honor pre-scope database records only for the original owner.
            legacy_idem = conn.execute(
                """SELECT receipt_json FROM lane_checkouts
                   WHERE idempotency_key = ? AND principal = ? AND session_id = ?""",
                (idempotency_key, principal, session_id),
            ).fetchone()
            if legacy_idem is not None:
                return json.loads(legacy_idem["receipt_json"])

            conn.execute(
                "UPDATE lane_checkouts SET status='expired' WHERE status='active' AND expires_at <= ?",
                (ts,),
            )

            if lane_id is None and role is None:
                active_self = conn.execute(
                    "SELECT receipt_json FROM lane_checkouts WHERE session_id = ? AND status = 'active'",
                    (session_id,),
                ).fetchone()
                if active_self is not None:
                    return json.loads(active_self["receipt_json"])

            if lane_id is not None:
                row = conn.execute(
                    "SELECT * FROM lane_assignments WHERE lane_id = ? AND enabled = 1",
                    (lane_id,),
                ).fetchone()
                if row is None:
                    raise LaneConflictError(f"Lane {lane_id!r} not found or disabled")
                held = conn.execute(
                    "SELECT session_id FROM lane_checkouts WHERE lane_id = ? AND status = 'active'",
                    (lane_id,),
                ).fetchone()
                if held is not None:
                    if held["session_id"] == session_id:
                        rec = conn.execute(
                            "SELECT receipt_json FROM lane_checkouts WHERE lane_id = ? AND status = 'active'",
                            (lane_id,),
                        ).fetchone()
                        assert rec is not None
                        return json.loads(rec["receipt_json"])
                    raise LaneConflictError(f"Lane {lane_id!r} already held by {held['session_id']}")
                target = row
            else:
                query = """
                    SELECT a.* FROM lane_assignments a
                    LEFT JOIN lane_checkouts c
                      ON c.lane_id = a.lane_id AND c.status = 'active'
                    WHERE a.enabled = 1 AND c.checkout_id IS NULL
                """
                params: list[Any] = []
                if role is not None:
                    query += " AND a.role = ?"
                    params.append(role)
                query += " ORDER BY a.lane_id LIMIT 1"
                target = conn.execute(query, params).fetchone()
                if target is None:
                    raise LaneConflictError("No eligible unclaimed lane available")

            fence_row = conn.execute(
                "SELECT COALESCE(MAX(fence_token), 0) + 1 AS next_fence FROM lane_checkouts WHERE lane_id = ?",
                (target["lane_id"],),
            ).fetchone()
            fence_token = int(fence_row["next_fence"])
            checkout_id = f"lchk-{uuid.uuid4().hex[:12]}"
            expires_at = ts + ttl
            receipt = {
                "checkout_id": checkout_id,
                "lane_id": target["lane_id"],
                "role": target["role"],
                "repos": json.loads(target["repos_json"]),
                "principal": principal,
                "session_id": session_id,
                "idempotency_key": idempotency_key,
                "fence_token": fence_token,
                "status": "active",
                "ttl_sec": ttl,
                "expires_at": expires_at,
                "heartbeat_at": ts,
                "progress_at": None,
                "release_sha": target["release_sha"],
                "package_hash": target["package_hash"],
                "subagent_model": target["subagent_model"],
                "max_children": target["max_children"],
                "task_focus": target["task_focus"],
            }
            conn.execute(
                """
                INSERT INTO lane_checkouts (
                    checkout_id, lane_id, role, session_id, principal, idempotency_key,
                    fence_token, status, ttl_sec, expires_at, heartbeat_at, progress_at,
                    release_sha, package_hash, receipt_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    checkout_id,
                    target["lane_id"],
                    target["role"],
                    session_id,
                    principal,
                    scoped_idempotency_key,
                    fence_token,
                    ttl,
                    expires_at,
                    ts,
                    target["release_sha"],
                    target["package_hash"],
                    json.dumps(receipt),
                    ts,
                ),
            )
            return receipt

        return self._run_tx(_tx)

    def heartbeat(
        self,
        *,
        checkout_id: str,
        fence_token: int,
        session_id: str,
        ttl_sec: int = _DEFAULT_TTL_SEC,
        progress: bool = False,
        now: float | None = None,
    ) -> dict[str, Any]:
        ts = time.time() if now is None else float(now)
        ttl = max(60, int(ttl_sec))

        def _tx(conn: sqlite3.Connection) -> dict[str, Any]:
            row = conn.execute(
                "SELECT * FROM lane_checkouts WHERE checkout_id = ?",
                (checkout_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "active"
                or int(row["fence_token"]) != int(fence_token)
                or row["session_id"] != session_id
            ):
                raise StaleLaneFenceError(f"Stale or invalid checkout {checkout_id!r}")
            new_exp = ts + ttl
            new_prog = ts if progress else row["progress_at"]
            receipt = json.loads(row["receipt_json"])
            receipt["expires_at"] = new_exp
            receipt["heartbeat_at"] = ts
            receipt["progress_at"] = new_prog
            conn.execute(
                """
                UPDATE lane_checkouts
                SET expires_at = ?, heartbeat_at = ?, progress_at = ?, receipt_json = ?
                WHERE checkout_id = ?
                """,
                (new_exp, ts, new_prog, json.dumps(receipt), checkout_id),
            )
            return receipt

        return self._run_tx(_tx)

    def release(
        self,
        *,
        checkout_id: str,
        fence_token: int,
        session_id: str,
        reason: str = "completed",
        now: float | None = None,
    ) -> dict[str, Any]:
        ts = time.time() if now is None else float(now)

        def _tx(conn: sqlite3.Connection) -> dict[str, Any]:
            row = conn.execute(
                "SELECT * FROM lane_checkouts WHERE checkout_id = ?",
                (checkout_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "active"
                or int(row["fence_token"]) != int(fence_token)
                or row["session_id"] != session_id
            ):
                raise StaleLaneFenceError(f"Stale or invalid checkout {checkout_id!r}")
            receipt = json.loads(row["receipt_json"])
            receipt["status"] = "released"
            receipt["released_at"] = ts
            receipt["release_reason"] = reason
            conn.execute(
                "UPDATE lane_checkouts SET status = 'released', receipt_json = ? WHERE checkout_id = ?",
                (json.dumps(receipt), checkout_id),
            )
            return receipt

        return self._run_tx(_tx)

    def health_summary(
        self,
        *,
        stall_heartbeat_sec: int = 900,
        stall_progress_sec: int = 1800,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Classify active, stalled, expired, and unassigned idle lanes."""
        ts = time.time() if now is None else float(now)
        hb_thresh = max(60, int(stall_heartbeat_sec))
        prog_thresh = max(60, int(stall_progress_sec))

        def _tx(conn: sqlite3.Connection) -> dict[str, Any]:
            conn.execute(
                "UPDATE lane_checkouts SET status='expired' WHERE status='active' AND expires_at <= ?",
                (ts,),
            )
            assignments = conn.execute(
                "SELECT lane_id, role, task_focus, enabled FROM lane_assignments ORDER BY lane_id"
            ).fetchall()
            active_rows = conn.execute("SELECT * FROM lane_checkouts WHERE status='active' ORDER BY lane_id").fetchall()
            expired_rows = conn.execute(
                """
                SELECT lane_id, checkout_id, session_id, expires_at
                FROM lane_checkouts
                WHERE status='expired'
                ORDER BY expires_at DESC
                LIMIT 25
                """
            ).fetchall()

            active_lane_ids: set[str] = set()
            healthy_active: list[dict[str, Any]] = []
            stalled_heartbeat: list[dict[str, Any]] = []
            stalled_progress: list[dict[str, Any]] = []

            for r in active_rows:
                active_lane_ids.add(r["lane_id"])
                hb_age = ts - float(r["heartbeat_at"])
                prog_ref = float(r["progress_at"]) if r["progress_at"] is not None else float(r["created_at"])
                prog_age = ts - prog_ref
                item = {
                    "lane_id": r["lane_id"],
                    "checkout_id": r["checkout_id"],
                    "session_id": r["session_id"],
                    "heartbeat_age_sec": round(hb_age, 1),
                    "progress_age_sec": round(prog_age, 1),
                    "expires_in_sec": round(float(r["expires_at"]) - ts, 1),
                }
                if hb_age >= hb_thresh:
                    stalled_heartbeat.append(item)
                elif prog_age >= prog_thresh:
                    stalled_progress.append(item)
                else:
                    healthy_active.append(item)

            idle_lanes = [
                {
                    "lane_id": a["lane_id"],
                    "role": a["role"],
                    "task_focus": a["task_focus"],
                }
                for a in assignments
                if a["enabled"] and a["lane_id"] not in active_lane_ids
            ]
            expired_recent = [
                {
                    "lane_id": r["lane_id"],
                    "checkout_id": r["checkout_id"],
                    "session_id": r["session_id"],
                    "expired_ago_sec": round(ts - float(r["expires_at"]), 1),
                }
                for r in expired_rows
            ]
            return {
                "checked_at": ts,
                "healthy_active": healthy_active,
                "stalled_heartbeat": stalled_heartbeat,
                "stalled_progress": stalled_progress,
                "expired_recent": expired_recent,
                "idle_lanes": idle_lanes,
                "needs_attention": bool(stalled_heartbeat or stalled_progress or expired_recent or idle_lanes),
            }

        return self._run_tx(_tx)


_default_store_lock = threading.Lock()
_default_store: LanePromptStore | None = None


def get_lane_prompt_store() -> LanePromptStore:
    """Return the process-wide LanePromptStore singleton."""
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = LanePromptStore()
        return _default_store
