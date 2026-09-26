"""Work-item ledger and state tracking for Staff Console (SC-C3, Issue #1316).

Tracks every request Barb (or operators/roles) dispatches to a terminal state
(done, cancelled, escalated). Links runs, issues, PRs, and code requests to work items.
Enforces SLA deadlines, state machine transitions, and SC-A8 audit logging.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from staff.audit import record_audit
from staff.store import _now as _now_iso
from staff.store import default_db_path

log = logging.getLogger("dashboard.staff.work_items")

WORK_ITEM_STATES: tuple[str, ...] = (
    "open",
    "in_progress",
    "waiting_on_user",
    "waiting_on_ci",
    "blocked",
    "done",
    "cancelled",
    "escalated",
)

TERMINAL_STATES: frozenset[str] = frozenset({"done", "cancelled", "escalated"})

VALID_TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_progress", "blocked", "cancelled", "escalated"},
    "in_progress": {"waiting_on_user", "waiting_on_ci", "blocked", "done", "cancelled", "escalated"},
    "waiting_on_user": {"in_progress", "blocked", "cancelled", "escalated"},
    "waiting_on_ci": {"in_progress", "done", "blocked", "cancelled", "escalated"},
    "blocked": {"in_progress", "cancelled", "escalated"},
    "done": set(),
    "cancelled": set(),
    "escalated": {"in_progress", "done", "cancelled"},
}


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal work item state transition is attempted."""

    def __init__(self, current: str, target: str, message: str = "") -> None:
        msg = message or f"Cannot transition work item from '{current}' to '{target}'"
        super().__init__(msg)
        self.current = current
        self.target = target


@dataclass(frozen=True)
class WorkItemRecord:
    """Represents a discrete tracked unit of work dispatched across the fleet."""

    id: str
    title: str
    requested_by: str
    thread_id: str = ""
    owner_role: str = ""
    state: str = "open"
    expected_by: str | None = None
    links: dict[str, list[str]] = field(default_factory=dict)
    last_progress_at: str = ""
    next_check_at: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "requested_by": self.requested_by,
            "thread_id": self.thread_id,
            "owner_role": self.owner_role,
            "state": self.state,
            "expected_by": self.expected_by,
            "links": self.links,
            "last_progress_at": self.last_progress_at,
            "next_check_at": self.next_check_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> WorkItemRecord:
        try:
            links = json.loads(row["links_json"] or "{}")
        except Exception:
            links = {}
        return cls(
            id=row["id"],
            title=row["title"],
            requested_by=row["requested_by"],
            thread_id=row["thread_id"] or "",
            owner_role=row["owner_role"] or "",
            state=row["state"] or "open",
            expected_by=row["expected_by"],
            links=links,
            last_progress_at=row["last_progress_at"] or "",
            next_check_at=row["next_check_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class WorkItemStore:
    """SQLite-backed persistent store for work items."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or default_db_path()
        self._lock = threading.RLock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS work_items (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        requested_by TEXT NOT NULL,
                        thread_id TEXT NOT NULL DEFAULT '',
                        owner_role TEXT NOT NULL DEFAULT '',
                        state TEXT NOT NULL DEFAULT 'open',
                        expected_by TEXT,
                        links_json TEXT NOT NULL DEFAULT '{}',
                        last_progress_at TEXT NOT NULL,
                        next_check_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_work_items_thread ON work_items(thread_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_work_items_state ON work_items(state)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_work_items_requested ON work_items(requested_by)")
                conn.commit()
            finally:
                conn.close()

    def create_work_item(
        self,
        title: str,
        requested_by: str,
        thread_id: str = "",
        owner_role: str = "",
        expected_by: str | None = None,
        links: dict[str, list[str]] | None = None,
        work_item_id: str | None = None,
        next_check_at: str | None = None,
    ) -> WorkItemRecord:
        """Create and persist a new work item."""
        wid = work_item_id or f"wi-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        init_links = links or {"runs": [], "issues": [], "prs": [], "code_requests": []}

        record = WorkItemRecord(
            id=wid,
            title=title,
            requested_by=requested_by,
            thread_id=thread_id,
            owner_role=owner_role,
            state="open",
            expected_by=expected_by,
            links=init_links,
            last_progress_at=now,
            next_check_at=next_check_at,
            created_at=now,
            updated_at=now,
        )

        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO work_items (
                        id, title, requested_by, thread_id, owner_role, state,
                        expected_by, links_json, last_progress_at, next_check_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.title,
                        record.requested_by,
                        record.thread_id,
                        record.owner_role,
                        record.state,
                        record.expected_by,
                        json.dumps(record.links),
                        record.last_progress_at,
                        record.next_check_at,
                        record.created_at,
                        record.updated_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        record_audit(
            action="work_item_create",
            principal=requested_by,
            target=wid,
            thread_id=thread_id,
            detail={"title": title, "owner_role": owner_role},
        )
        return record

    def get_work_item(self, work_item_id: str) -> WorkItemRecord | None:
        """Fetch work item by ID."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
                return WorkItemRecord.from_row(row) if row else None
            finally:
                conn.close()

    def list_work_items(
        self,
        thread_id: str | None = None,
        owner_role: str | None = None,
        requested_by: str | None = None,
        state: str | None = None,
        overdue: bool | None = None,
        limit: int = 100,
    ) -> list[WorkItemRecord]:
        """Query work items with filters."""
        query = "SELECT * FROM work_items WHERE 1=1"
        params: list[Any] = []

        if thread_id is not None:
            query += " AND thread_id = ?"
            params.append(thread_id)
        if owner_role is not None:
            query += " AND owner_role = ?"
            params.append(owner_role)
        if requested_by is not None:
            query += " AND requested_by = ?"
            params.append(requested_by)
        if state is not None:
            query += " AND state = ?"
            params.append(state)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(query, tuple(params)).fetchall()
                items = [WorkItemRecord.from_row(r) for r in rows]
            finally:
                conn.close()

        if overdue is True:
            now = _now_iso()
            items = [
                item
                for item in items
                if item.state not in TERMINAL_STATES and item.expected_by and item.expected_by < now
            ]
        elif overdue is False:
            now = _now_iso()
            items = [item for item in items if not (item.expected_by and item.expected_by < now)]

        return items

    def transition_state(
        self,
        work_item_id: str,
        target_state: str,
        actor: str = "",
        reason: str = "",
    ) -> WorkItemRecord:
        """Enforce state machine transition and record audit event."""
        if target_state not in WORK_ITEM_STATES:
            raise InvalidStateTransitionError("unknown", target_state, f"Invalid target state '{target_state}'")

        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
                if not row:
                    raise KeyError(f"Work item '{work_item_id}' not found")
                current = WorkItemRecord.from_row(row)

                if target_state not in VALID_TRANSITIONS.get(current.state, set()):
                    raise InvalidStateTransitionError(
                        current.state,
                        target_state,
                        f"Cannot transition work item {work_item_id} from '{current.state}' to '{target_state}'",
                    )

                now = _now_iso()
                conn.execute(
                    "UPDATE work_items SET state = ?, last_progress_at = ?, updated_at = ? WHERE id = ?",
                    (target_state, now, now, work_item_id),
                )
                conn.commit()
            finally:
                conn.close()

        record_audit(
            action="work_item_transition",
            principal=actor or current.requested_by,
            target=work_item_id,
            thread_id=current.thread_id,
            detail={"previous_state": current.state, "target_state": target_state, "reason": reason},
        )
        updated = self.get_work_item(work_item_id)
        assert updated is not None  # noqa: S101
        return updated

    def add_link(self, work_item_id: str, link_kind: str, link_value: str) -> WorkItemRecord:
        """Append a run, PR, issue, or code request link to a work item."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
                if not row:
                    raise KeyError(f"Work item '{work_item_id}' not found")
                current = WorkItemRecord.from_row(row)

                links = dict(current.links)
                items = list(links.get(link_kind, []))
                if link_value not in items:
                    items.append(link_value)
                links[link_kind] = items
                now = _now_iso()

                conn.execute(
                    "UPDATE work_items SET links_json = ?, last_progress_at = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(links), now, now, work_item_id),
                )
                conn.commit()
            finally:
                conn.close()

        updated = self.get_work_item(work_item_id)
        assert updated is not None  # noqa: S101
        return updated

    def update_work_item(
        self,
        work_item_id: str,
        title: str | None = None,
        owner_role: str | None = None,
        expected_by: str | None = None,
        next_check_at: str | None = None,
    ) -> WorkItemRecord:
        """Update mutable work item metadata fields."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
                if not row:
                    raise KeyError(f"Work item '{work_item_id}' not found")

                updates: list[str] = []
                params: list[Any] = []
                now = _now_iso()

                if title is not None:
                    updates.append("title = ?")
                    params.append(title)
                if owner_role is not None:
                    updates.append("owner_role = ?")
                    params.append(owner_role)
                if expected_by is not None:
                    updates.append("expected_by = ?")
                    params.append(expected_by)
                if next_check_at is not None:
                    updates.append("next_check_at = ?")
                    params.append(next_check_at)

                updates.append("updated_at = ?")
                params.append(now)
                params.append(work_item_id)

                sql = f"UPDATE work_items SET {', '.join(updates)} WHERE id = ?"
                conn.execute(sql, tuple(params))
                conn.commit()
            finally:
                conn.close()

        updated = self.get_work_item(work_item_id)
        assert updated is not None  # noqa: S101
        return updated


_WORK_ITEM_STORE: WorkItemStore | None = None
_STORE_LOCK = threading.Lock()


def get_work_item_store(db_path: Path | None = None) -> WorkItemStore:
    """Retrieve singleton WorkItemStore instance."""
    global _WORK_ITEM_STORE
    with _STORE_LOCK:
        if _WORK_ITEM_STORE is None or (db_path is not None and _WORK_ITEM_STORE.db_path != db_path):
            _WORK_ITEM_STORE = WorkItemStore(db_path)
        return _WORK_ITEM_STORE


def reset_work_item_store() -> None:
    """Reset singleton instance (useful in tests)."""
    global _WORK_ITEM_STORE
    with _STORE_LOCK:
        _WORK_ITEM_STORE = None
