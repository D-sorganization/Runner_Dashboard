"""Durable, append-only staff audit log (SC-A8, Issue #1298).

Records every staff dispatch, cancel, hold mutation, schedule toggle,
action proposal, maintenance action, and routing decision to a node-local
SQLite table with retention and gzip archival.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from identity import SCOPE_PRESETS
from staff.store import _config_dir, _now

# Ensure staff.audit.read scope is granted to operator role
if "staff.audit.read" not in SCOPE_PRESETS.get("operator", []):
    SCOPE_PRESETS.setdefault("operator", []).append("staff.audit.read")

log = logging.getLogger("dashboard.staff.audit")

ALLOWED_SURFACES = frozenset({"ui", "api", "mcp", "barb", "scheduler", "thread"})

ALLOWED_ACTIONS = frozenset(
    {
        "dispatch",
        "cancel",
        "hold_set",
        "hold_clear",
        "schedule_toggle",
        "proposal_create",
        "proposal_approve",
        "proposal_deny",
        "proposal_execute",
        "maintenance",
        "routing",
        "thread_create",
        "thread_archive",
        "work_item_create",
        "work_item_transition",
        "message_reconcile",
    }
)

MUTATING_ACTIONS = frozenset(
    {
        "dispatch",
        "cancel",
        "hold_set",
        "hold_clear",
        "schedule_toggle",
        "proposal_create",
        "proposal_approve",
        "proposal_deny",
        "proposal_execute",
        "maintenance",
        "thread_create",
        "thread_archive",
        "work_item_create",
        "work_item_transition",
        "message_reconcile",
    }
)


class AuditError(RuntimeError):
    """Raised when a mutating staff action fails to write its audit row (fail closed)."""


def default_audit_db_path() -> Path:
    return Path(os.environ.get("STAFF_RUNS_DB", str(_config_dir() / "staff_runs.sqlite3"))).expanduser()


@dataclass
class StaffAuditRecord:
    """Flat representation of an audit row for a staff operation."""

    id: int | None = None
    ts: str = field(default_factory=_now)
    principal: str = ""
    on_behalf_of: str = ""
    surface: str = "api"
    action: str = ""
    target: str = ""
    request_id: str = ""
    thread_id: str = ""
    run_id: str = ""
    outcome: str = "success"
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "principal": self.principal,
            "on_behalf_of": self.on_behalf_of,
            "surface": self.surface,
            "action": self.action,
            "target": self.target,
            "request_id": self.request_id,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "outcome": self.outcome,
            "detail": self.detail,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> StaffAuditRecord:
        d = dict(row)
        raw_detail = d.get("detail", "{}")
        if isinstance(raw_detail, str):
            try:
                detail = json.loads(raw_detail)
            except json.JSONDecodeError:
                detail = {"raw": raw_detail}
        elif isinstance(raw_detail, dict):
            detail = raw_detail
        else:
            detail = {}
        return cls(
            id=d.get("id"),
            ts=str(d.get("ts", "")),
            principal=str(d.get("principal", "")),
            on_behalf_of=str(d.get("on_behalf_of", "")),
            surface=str(d.get("surface", "api")),
            action=str(d.get("action", "")),
            target=str(d.get("target", "")),
            request_id=str(d.get("request_id", "")),
            thread_id=str(d.get("thread_id", "")),
            run_id=str(d.get("run_id", "")),
            outcome=str(d.get("outcome", "success")),
            detail=detail,
        )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS staff_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    principal TEXT NOT NULL DEFAULT '',
    on_behalf_of TEXT NOT NULL DEFAULT '',
    surface TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    thread_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS staff_audit_ts_idx ON staff_audit(ts);
CREATE INDEX IF NOT EXISTS staff_audit_principal_idx ON staff_audit(principal);
CREATE INDEX IF NOT EXISTS staff_audit_thread_idx ON staff_audit(thread_id);
CREATE INDEX IF NOT EXISTS staff_audit_action_idx ON staff_audit(action);
CREATE INDEX IF NOT EXISTS staff_audit_run_idx ON staff_audit(run_id);
"""


class StaffAuditStore:
    """Thread-safe SQLite store for append-only staff audit rows."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_audit_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA busy_timeout = 30000")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)

    def columns(self) -> set[str]:
        with self._lock:
            return {str(r["name"]) for r in self._conn.execute("PRAGMA table_info(staff_audit)").fetchall()}

    def record(self, rec: StaffAuditRecord, fail_closed: bool | None = None) -> int:
        """Record an audit row.

        If fail_closed is True (or action is mutating when unset), a database
        failure raises AuditError to reject the mutating operation.
        """
        is_fail_closed = rec.action in MUTATING_ACTIONS if fail_closed is None else fail_closed
        surface = rec.surface if rec.surface in ALLOWED_SURFACES else "api"
        detail_json = json.dumps(rec.detail, separators=(",", ":"))
        try:
            with self._lock:
                cursor = self._conn.execute(
                    "INSERT INTO staff_audit (ts, principal, on_behalf_of, surface, action, target, "
                    "request_id, thread_id, run_id, outcome, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        rec.ts or _now(),
                        rec.principal,
                        rec.on_behalf_of,
                        surface,
                        rec.action,
                        rec.target,
                        rec.request_id,
                        rec.thread_id,
                        rec.run_id,
                        rec.outcome,
                        detail_json,
                    ),
                )
                audit_id = int(cursor.lastrowid or 0)
                rec.id = audit_id
                return audit_id
        except Exception as exc:
            if is_fail_closed:
                log.error("Mutating staff action audit write failed (failing closed): %s", exc)
                raise AuditError(f"Staff audit write failed: {exc}") from exc
            log.error("Read-only staff action audit write failed: %s", exc)
            return -1

    def list_entries(
        self,
        limit: int = 50,
        offset: int = 0,
        principal: str | None = None,
        thread_id: str | None = None,
        run_id: str | None = None,
        action: str | None = None,
        surface: str | None = None,
        target: str | None = None,
        since: str | None = None,
    ) -> list[StaffAuditRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if principal:
            clauses.append("principal = ?")
            params.append(principal)
        if thread_id:
            clauses.append("thread_id = ?")
            params.append(thread_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if action:
            clauses.append("action = ?")
            params.append(action)
        if surface:
            clauses.append("surface = ?")
            params.append(surface)
        if target:
            clauses.append("target = ?")
            params.append(target)
        if since:
            clauses.append("ts >= ?")
            params.append(since)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM staff_audit {where} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?"  # noqa: S608
        params.extend([int(limit), int(offset)])

        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [StaffAuditRecord.from_row(r) for r in rows]

    def count_entries(
        self,
        principal: str | None = None,
        thread_id: str | None = None,
        run_id: str | None = None,
        action: str | None = None,
        surface: str | None = None,
        target: str | None = None,
        since: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if principal:
            clauses.append("principal = ?")
            params.append(principal)
        if thread_id:
            clauses.append("thread_id = ?")
            params.append(thread_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if action:
            clauses.append("action = ?")
            params.append(action)
        if surface:
            clauses.append("surface = ?")
            params.append(surface)
        if target:
            clauses.append("target = ?")
            params.append(target)
        if since:
            clauses.append("ts >= ?")
            params.append(since)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT COUNT(*) FROM staff_audit {where}"  # noqa: S608

        with self._lock:
            row = self._conn.execute(query, tuple(params)).fetchone()
            return int(row[0]) if row else 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_audit_store: StaffAuditStore | None = None
_audit_store_lock = threading.Lock()


def get_audit_store() -> StaffAuditStore:
    global _audit_store  # noqa: PLW0603
    with _audit_store_lock:
        if _audit_store is None or _audit_store.path != default_audit_db_path():
            _audit_store = StaffAuditStore()
        return _audit_store


def reset_audit_store() -> None:
    global _audit_store  # noqa: PLW0603
    with _audit_store_lock:
        if _audit_store is not None:
            _audit_store.close()
        _audit_store = None


def record_audit(
    *,
    action: str,
    target: str = "",
    principal: str = "",
    on_behalf_of: str = "",
    surface: str = "api",
    request_id: str = "",
    thread_id: str = "",
    run_id: str = "",
    outcome: str = "success",
    detail: dict[str, Any] | None = None,
    fail_closed: bool | None = None,
    store: StaffAuditStore | None = None,
) -> int:
    """Helper to record a staff audit entry in the default store."""
    rec = StaffAuditRecord(
        action=action,
        target=target,
        principal=principal,
        on_behalf_of=on_behalf_of,
        surface=surface,
        request_id=request_id,
        thread_id=thread_id,
        run_id=run_id,
        outcome=outcome,
        detail=detail or {},
    )
    target_store = store or get_audit_store()
    return target_store.record(rec, fail_closed=fail_closed)


def archive_old_audit_entries(
    store: StaffAuditStore | None = None,
    retention_days: int = 180,
    archive_dir: Path | None = None,
    reference_date: str | None = None,
) -> tuple[int, list[Path]]:
    """Archive audit rows older than retention_days to gzip and delete them from DB.

    Never deletes rows without first writing them to a verified gzip archive.
    """
    target_store = store or get_audit_store()
    out_dir = archive_dir or (_config_dir() / "audit_archive")
    out_dir.mkdir(parents=True, exist_ok=True)

    if reference_date:
        ref_dt = datetime.fromisoformat(reference_date.replace("Z", "+00:00"))
    else:
        ref_dt = datetime.now(UTC)
    cutoff = ref_dt - timedelta(days=retention_days)
    cutoff_iso = cutoff.isoformat().replace("+00:00", "Z")

    with target_store._lock:  # noqa: SLF001
        rows = target_store._conn.execute(  # noqa: SLF001
            "SELECT * FROM staff_audit WHERE ts < ? ORDER BY ts ASC",
            (cutoff_iso,),
        ).fetchall()
        if not rows:
            return 0, []

        records = [StaffAuditRecord.from_row(r) for r in rows]
        archive_name = f"staff_audit_archive_{cutoff.strftime('%Y%m')}.jsonl.gz"
        archive_path = out_dir / archive_name

        with gzip.open(archive_path, "at", encoding="utf-8") as gz:
            for rec in records:
                gz.write(json.dumps(rec.to_dict(), separators=(",", ":")) + "\n")

        ids_to_delete = [r.id for r in records if r.id is not None]
        chunk_size = 500
        for i in range(0, len(ids_to_delete), chunk_size):
            chunk = ids_to_delete[i : i + chunk_size]
            marks = ", ".join("?" for _ in chunk)
            target_store._conn.execute(  # noqa: SLF001
                f"DELETE FROM staff_audit WHERE id IN ({marks})",  # noqa: S608
                tuple(chunk),
            )

    log.info("Archived %d audit entries to %s", len(records), archive_path)
    return len(records), [archive_path]


def export_audit_csv(records: list[StaffAuditRecord]) -> str:
    """Format records as RFC 4180 CSV string."""
    buf = io.StringIO()
    fields = [
        "id",
        "ts",
        "principal",
        "on_behalf_of",
        "surface",
        "action",
        "target",
        "request_id",
        "thread_id",
        "run_id",
        "outcome",
        "detail",
    ]
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for rec in records:
        d = rec.to_dict()
        d["detail"] = json.dumps(d["detail"], separators=(",", ":"))
        writer.writerow(d)
    return buf.getvalue()


def export_audit_ndjson(records: list[StaffAuditRecord]) -> str:
    """Format records as line-delimited JSON."""
    buf = io.StringIO()
    for rec in records:
        buf.write(json.dumps(rec.to_dict(), separators=(",", ":")) + "\n")
    return buf.getvalue()
