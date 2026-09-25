"""Node-local SQLite store for staff runs and their streamed events.

Design (ADR 0003 "laptop-runnable"): one file under the dashboard config dir,
WAL mode, a single process-wide connection guarded by a lock. The hub merges
per-node stores over HTTP; nothing here is shared across machines.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUN_STATUSES = (
    "queued",
    "preparing",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "blocked",
)
ACTIVE_STATUSES = ("queued", "preparing", "running")


def _config_dir() -> Path:
    configured = os.environ.get("RUNNER_DASHBOARD_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path("~/.config/runner-dashboard").expanduser()


def default_db_path() -> Path:
    return Path(os.environ.get("STAFF_RUNS_DB", str(_config_dir() / "staff_runs.sqlite3"))).expanduser()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class RunRecord:
    """Flat row for one staff run (LoD: no nested objects cross the API)."""

    id: str
    role: str
    provider: str
    model: str | None
    machine: str
    repo: str
    target_kind: str  # issue | pr | prompt
    target_ref: str
    prompt: str
    status: str = "queued"
    requested_by: str = ""
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    ended_at: str | None = None
    exit_code: int | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    workdir: str = ""
    branch: str = ""
    transcript_path: str = ""
    lease_id: str = ""
    error: str = ""
    last_line: str = ""
    # How cost_usd was obtained: reported | token_table | wall_time | none | "" (not finalised). Issue #1200.
    cost_method: str = ""
    # PR-consolidation strategy (issue #1213): "consolidate" | "serial" | "" (role has no strategy or no repo);
    # ``outcome`` is the normalised "consolidated N PRs into #M" parsed from the final STAFF_RESULT: line.
    strategy_mode: str = ""
    outcome: str = ""
    # Failure classification (issue #1293, SC-A4 / SC-A6): "orphaned" | "timeout" | "stalled" | ""
    failure_class: str = ""
    # Process ID of executing CLI worker (issue #1293)
    pid: int | None = None
    # Retryable indicator and remediation guidance (issue #1297, SC-A6)
    retryable: bool = False
    remediation: str = ""
    on_behalf_of: str = ""

    def __post_init__(self) -> None:
        self.retryable = bool(self.retryable)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["retryable"] = bool(self.retryable)
        return d


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    machine TEXT NOT NULL,
    repo TEXT NOT NULL DEFAULT '',
    target_kind TEXT NOT NULL,
    target_ref TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    exit_code INTEGER,
    cost_usd REAL NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    workdir TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT '',
    transcript_path TEXT NOT NULL DEFAULT '',
    lease_id TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    last_line TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS runs_status_idx ON runs(status);
CREATE INDEX IF NOT EXISTS runs_created_idx ON runs(created_at);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run_idx ON events(run_id, seq);
"""

_COLUMNS = tuple(RunRecord.__dataclass_fields__.keys())

# Columns added after the first schema shipped. Applied with a guarded
# ``ALTER TABLE ... ADD COLUMN`` so an existing store upgrades in place and a
# rollback to the previous code keeps working (extra columns are ignored).
_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("cost_method", "TEXT NOT NULL DEFAULT ''"),
    ("strategy_mode", "TEXT NOT NULL DEFAULT ''"),
    ("outcome", "TEXT NOT NULL DEFAULT ''"),
    ("failure_class", "TEXT NOT NULL DEFAULT ''"),
    ("pid", "INTEGER"),
    ("retryable", "INTEGER NOT NULL DEFAULT 0"),
    ("remediation", "TEXT NOT NULL DEFAULT ''"),
    ("on_behalf_of", "TEXT NOT NULL DEFAULT ''"),
)

USAGE_GROUPS = ("provider", "role", "day")
_USAGE_GROUP_SQL = {
    "provider": "provider",
    "role": "role",
    "day": "substr(created_at, 1, 10)",
}


class RunStore:
    """Thread-safe SQLite-backed run + event store."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._migrate()
        self._seq: dict[str, int] = {}

    def _migrate(self) -> None:
        """Add any column in ``_ADDED_COLUMNS`` that the on-disk table lacks (idempotent)."""
        present = {str(r["name"]) for r in self._conn.execute("PRAGMA table_info(runs)").fetchall()}
        for name, decl in _ADDED_COLUMNS:
            if name not in present:
                self._conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {decl}")  # noqa: S608

    def columns(self) -> set[str]:
        """Column names currently present on the ``runs`` table (for migration tests)."""
        with self._lock:
            return {str(r["name"]) for r in self._conn.execute("PRAGMA table_info(runs)").fetchall()}

    # ── runs ─────────────────────────────────────────────────────────────
    def create_run(self, rec: RunRecord) -> RunRecord:
        assert rec.status in RUN_STATUSES, rec.status  # noqa: S101
        cols = ", ".join(_COLUMNS)
        marks = ", ".join("?" for _ in _COLUMNS)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO runs ({cols}) VALUES ({marks})",
                tuple(getattr(rec, c) for c in _COLUMNS),
            )  # noqa: S608
        return rec

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        unknown = set(fields) - set(_COLUMNS)
        assert not unknown, f"unknown columns: {unknown}"  # noqa: S101
        if "status" in fields:
            assert fields["status"] in RUN_STATUSES, fields["status"]  # noqa: S101
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE runs SET {sets} WHERE id = ?", (*fields.values(), run_id))  # noqa: S608

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return RunRecord(**dict(row)) if row else None

    def list_runs(
        self,
        limit: int = 50,
        role: str | None = None,
        status: str | None = None,
        since: str | None = None,
    ) -> list[RunRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if role:
            clauses.append("role = ?")
            params.append(role)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if since:
            clauses.append("created_at >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM runs {where} ORDER BY created_at DESC LIMIT ?",  # noqa: S608
                (*params, int(limit)),
            ).fetchall()
        return [RunRecord(**dict(r)) for r in rows]

    def active_runs(self) -> list[RunRecord]:
        marks = ", ".join("?" for _ in ACTIVE_STATUSES)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM runs WHERE status IN ({marks}) ORDER BY created_at",
                ACTIVE_STATUSES,
            ).fetchall()  # noqa: S608
        return [RunRecord(**dict(r)) for r in rows]

    def spend_since(self, since_iso: str) -> dict[str, float]:
        """Cost per provider for runs created at/after ``since_iso`` plus a total."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT provider, COALESCE(SUM(cost_usd), 0) AS usd FROM runs WHERE created_at >= ? GROUP BY provider",
                (since_iso,),
            ).fetchall()
        out = {str(r["provider"]): float(r["usd"]) for r in rows}
        out["total"] = round(sum(out.values()), 6)
        return out

    def spend_by_role_since(self, since_iso: str) -> dict[str, float]:
        """Cost per role for runs created at/after ``since_iso`` (issue #1196 budgets)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, COALESCE(SUM(cost_usd), 0) AS usd FROM runs WHERE created_at >= ? GROUP BY role",
                (since_iso,),
            ).fetchall()
        return {str(r["role"]): float(r["usd"]) for r in rows}

    def usage_by(self, group: str = "provider", since: str | None = None) -> list[dict[str, Any]]:
        """Aggregate runs by ``provider``, ``role`` or ``day`` (issue #1200).

        Pre: ``group`` in ``USAGE_GROUPS``. Post: one row per key, ordered by key,
        with ``runs``, ``cost_usd``, ``input_tokens``, ``output_tokens`` and
        ``wall_seconds`` (sum of ended_at - started_at over runs that have both).
        """
        assert group in USAGE_GROUPS, group  # noqa: S101
        key_sql = _USAGE_GROUP_SQL[group]
        where = "WHERE created_at >= ?" if since else ""
        params: tuple[Any, ...] = (since,) if since else ()
        sql = (
            f"SELECT {key_sql} AS key, COUNT(*) AS runs, COALESCE(SUM(cost_usd), 0) AS cost_usd, "
            "COALESCE(SUM(input_tokens), 0) AS input_tokens, COALESCE(SUM(output_tokens), 0) AS output_tokens, "
            "COALESCE(SUM(CASE WHEN started_at IS NOT NULL AND ended_at IS NOT NULL "
            "THEN (julianday(ended_at) - julianday(started_at)) * 86400.0 ELSE 0 END), 0) AS wall_seconds "
            f"FROM runs {where} GROUP BY key ORDER BY key"  # noqa: S608
        )
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "key": str(r["key"]),
                "runs": int(r["runs"]),
                "cost_usd": round(float(r["cost_usd"]), 6),
                "input_tokens": int(r["input_tokens"]),
                "output_tokens": int(r["output_tokens"]),
                "wall_seconds": round(max(0.0, float(r["wall_seconds"])), 1),
            }
            for r in rows
        ]

    # ── events ───────────────────────────────────────────────────────────
    def append_event(self, run_id: str, kind: str, text: str) -> int:
        with self._lock:
            seq = self._seq.get(run_id)
            if seq is None:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM events WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                seq = int(row[0])
            seq += 1
            self._seq[run_id] = seq
            self._conn.execute(
                "INSERT INTO events (run_id, seq, ts, kind, text) VALUES (?, ?, ?, ?, ?)",
                (run_id, seq, _now(), kind, text[:4000]),
            )
            if text.strip():
                self._conn.execute(
                    "UPDATE runs SET last_line = ? WHERE id = ?",
                    (text.strip()[:300], run_id),
                )
        return seq

    def events_after(self, run_id: str, after_seq: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, ts, kind, text FROM events WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (run_id, int(after_seq), int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_store: RunStore | None = None
_store_lock = threading.Lock()


def get_store() -> RunStore:
    """Process-wide store (path resolved lazily so tests can point it at tmp)."""
    global _store  # noqa: PLW0603
    with _store_lock:
        if _store is None or _store.path != default_db_path():
            _store = RunStore()
        return _store


def reset_store() -> None:
    global _store  # noqa: PLW0603
    with _store_lock:
        if _store is not None:
            _store.close()
        _store = None
