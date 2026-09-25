"""Node-local SQLite store for staff conversations, threads, messages, and action proposals.

Persists threads, sequential messages, and action proposal state machines
in staff_runs.sqlite3 under SQLite WAL mode with threading.RLock() concurrency,
forward-only schema migrations, automatic pre-migration backups, and fail-safe
degraded mode banners (SC-B2, Issue #1305).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from staff import conversation_proposals as _proposals
from staff.audit import StaffAuditStore, record_audit
from staff.conversation_migrations import CORE_MIGRATIONS, run_migrations
from staff.conversation_models import (
    MESSAGE_AUTHOR_KINDS,
    MESSAGE_DELIVERIES,
    MESSAGE_KINDS,
    PROPOSAL_RISKS,
    PROPOSAL_STATES,
    THREAD_KINDS,
    THREAD_STATUSES,
    ActionProposalRecord,
    ConversationStoreStatus,
    ConversationsUnavailableError,
    MessageRecord,
    ThreadRecord,
    _now,
)
from staff.redaction import redact_sensitive_content
from staff.store import default_db_path

__all__ = [
    "MESSAGE_AUTHOR_KINDS",
    "MESSAGE_DELIVERIES",
    "MESSAGE_KINDS",
    "PROPOSAL_RISKS",
    "PROPOSAL_STATES",
    "THREAD_KINDS",
    "THREAD_STATUSES",
    "ActionProposalRecord",
    "ConversationStore",
    "ConversationStoreStatus",
    "ConversationsUnavailableError",
    "MessageRecord",
    "ThreadRecord",
    "get_conversation_store",
    "get_conversation_store_status",
    "reset_conversation_store",
]

log = logging.getLogger("dashboard.staff.conversations")


class ConversationStore:
    """Thread-safe, SQLite WAL-backed conversation and action proposal store."""

    MIGRATIONS: list[tuple[int, str, str]] = CORE_MIGRATIONS

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._audit_store = StaffAuditStore(self.path)
        self.status = ConversationStoreStatus()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self.status = run_migrations(self._conn, self.path, self.MIGRATIONS)

    def _ensure_available(self) -> None:
        if not self.status.available:
            raise ConversationsUnavailableError(
                self.status.banner_message or "Conversation store is disabled due to migration failure."
            )

    # ── THREADS ──────────────────────────────────────────────────────────────

    def create_thread(
        self,
        title: str,
        kind: str = "direct",
        participants: list[str] | None = None,
        created_by: str = "",
        thread_id: str | None = None,
        meta: dict[str, Any] | None = None,
        role: str | None = None,
    ) -> ThreadRecord:
        self._ensure_available()
        assert kind in THREAD_KINDS, f"Invalid thread kind: {kind}"  # noqa: S101
        tid = thread_id or f"th_{uuid.uuid4().hex[:12]}"
        parts = list(participants or [])
        if role and role not in parts:
            parts.append(role)
        unread = {p: 0 for p in parts}
        meta_dict = dict(meta or {})
        now = _now()
        rec = ThreadRecord(
            id=tid,
            title=title,
            kind=kind,
            participants=parts,
            created_by=created_by,
            status="open",
            unread_counters=unread,
            meta=meta_dict,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO threads (id, title, kind, participants, created_by, status, "
                "last_message_at, unread_counters, meta, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rec.id,
                    rec.title,
                    rec.kind,
                    json.dumps(rec.participants),
                    rec.created_by,
                    rec.status,
                    rec.last_message_at,
                    json.dumps(rec.unread_counters),
                    json.dumps(rec.meta),
                    rec.created_at,
                    rec.updated_at,
                ),
            )
        record_audit(
            action="thread_create",
            target=rec.id,
            principal=created_by,
            surface="thread",
            thread_id=rec.id,
            outcome="success",
            detail={"title": rec.title, "kind": rec.kind},
            fail_closed=False,
            store=self._audit_store,
        )
        return rec

    def get_thread(self, thread_id: str) -> ThreadRecord | None:
        self._ensure_available()
        with self._lock:
            row = self._conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        return ThreadRecord.from_row(row) if row else None

    def list_threads(
        self,
        kind: str | None = None,
        status: str | None = None,
        participant: str | None = None,
        limit: int = 50,
    ) -> list[ThreadRecord]:
        self._ensure_available()
        clauses: list[str] = []
        params: list[Any] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM threads {where} ORDER BY updated_at DESC LIMIT ?"  # noqa: S608
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()

        threads = [ThreadRecord.from_row(r) for r in rows]
        if participant:
            threads = [t for t in threads if participant in t.participants]
        return threads

    def update_thread(self, thread_id: str, **fields: Any) -> ThreadRecord | None:
        self._ensure_available()
        with self._lock:
            cur = self.get_thread(thread_id)
            if not cur:
                return None
            allowed = {"title", "status", "participants", "unread_counters", "meta"}
            updates: dict[str, Any] = {}
            for k, v in fields.items():
                if k in allowed:
                    updates[k] = json.dumps(v) if k in {"participants", "unread_counters", "meta"} else v
            if not updates:
                return cur
            updates["updated_at"] = _now()
            sets = ", ".join(f"{k} = ?" for k in updates)
            self._conn.execute(
                f"UPDATE threads SET {sets} WHERE id = ?",
                (*updates.values(), thread_id),
            )  # noqa: S608
            row = self._conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
            return ThreadRecord.from_row(row) if row else None

    def archive_thread(self, thread_id: str, principal: str = "") -> ThreadRecord | None:
        self._ensure_available()
        res = self.update_thread(thread_id, status="archived")
        if res:
            record_audit(
                action="thread_archive",
                target=thread_id,
                principal=principal,
                surface="thread",
                thread_id=thread_id,
                outcome="success",
                fail_closed=False,
                store=self._audit_store,
            )
        return res

    def mark_thread_read(self, thread_id: str, principal: str) -> None:
        self._ensure_available()
        with self._lock:
            thread = self.get_thread(thread_id)
            if not thread:
                return
            counters = dict(thread.unread_counters)
            counters[principal] = 0
            self._conn.execute(
                "UPDATE threads SET unread_counters = ? WHERE id = ?",
                (json.dumps(counters), thread_id),
            )

    # ── MESSAGES ─────────────────────────────────────────────────────────────

    def add_message(
        self,
        thread_id: str,
        author_kind: str = "user",
        author: str = "",
        kind: str = "text",
        body_md: str = "",
        meta: dict[str, Any] | None = None,
        run_id: str = "",
        idempotency_key: str | None = None,
        delivery: str = "complete",
        message_id: str | None = None,
    ) -> MessageRecord:
        self._ensure_available()
        assert author_kind in MESSAGE_AUTHOR_KINDS, f"Invalid author_kind: {author_kind}"  # noqa: S101
        assert kind in MESSAGE_KINDS, f"Invalid message kind: {kind}"  # noqa: S101
        assert delivery in MESSAGE_DELIVERIES, f"Invalid delivery: {delivery}"  # noqa: S101

        sanitized_body = redact_sensitive_content(body_md)
        mid = message_id or f"msg_{uuid.uuid4().hex[:12]}"
        meta_dict = dict(meta or {})

        with self._lock:
            for attempt in range(10):
                if idempotency_key:
                    existing = self._conn.execute(
                        "SELECT * FROM messages WHERE thread_id = ? AND idempotency_key = ?",
                        (thread_id, idempotency_key),
                    ).fetchone()
                    if existing:
                        return MessageRecord.from_row(existing)

                row_seq = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM messages WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
                seq = int(row_seq["max_seq"]) + 1 if row_seq else 1
                now = _now()

                try:
                    self._conn.execute(
                        "INSERT INTO messages (id, thread_id, seq, author_kind, author, kind, "
                        "body_md, meta, run_id, idempotency_key, created_at, delivery) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            mid,
                            thread_id,
                            seq,
                            author_kind,
                            author,
                            kind,
                            sanitized_body,
                            json.dumps(meta_dict),
                            run_id,
                            idempotency_key or None,
                            now,
                            delivery,
                        ),
                    )

                    th = self.get_thread(thread_id)
                    if th:
                        counters = dict(th.unread_counters)
                        for p in th.participants:
                            if p != author:
                                counters[p] = counters.get(p, 0) + 1
                        self._conn.execute(
                            "UPDATE threads SET last_message_at = ?, updated_at = ?, unread_counters = ? WHERE id = ?",
                            (now, now, json.dumps(counters), thread_id),
                        )
                    break
                except sqlite3.IntegrityError as exc:
                    if "messages.thread_id, messages.seq" in str(exc) and attempt < 9:
                        time.sleep(0.005 * (attempt + 1))
                        continue
                    raise

        return MessageRecord(
            id=mid,
            thread_id=thread_id,
            seq=seq,
            author_kind=author_kind,
            author=author,
            kind=kind,
            body_md=sanitized_body,
            meta=meta_dict,
            run_id=run_id,
            idempotency_key=idempotency_key,
            created_at=now,
            delivery=delivery,
        )

    def get_message(self, message_id: str) -> MessageRecord | None:
        self._ensure_available()
        with self._lock:
            row = self._conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return MessageRecord.from_row(row) if row else None

    def list_messages(self, thread_id: str, limit: int = 100, since_seq: int = 0) -> list[MessageRecord]:
        self._ensure_available()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE thread_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
                (thread_id, since_seq, limit),
            ).fetchall()
        return [MessageRecord.from_row(r) for r in rows]

    def update_message(self, message_id: str, **fields: Any) -> MessageRecord | None:
        """Update mutable fields of a message (body_md, kind, meta, delivery, run_id)."""
        self._ensure_available()
        with self._lock:
            cur = self.get_message(message_id)
            if not cur:
                return None
            allowed = {"body_md", "kind", "meta", "delivery", "run_id"}
            updates = {
                k: redact_sensitive_content(v or "") if k == "body_md" else json.dumps(v or {}) if k == "meta" else v
                for k, v in fields.items()
                if k in allowed
            }
            if not updates:
                return cur
            sets = ", ".join(f"{k} = ?" for k in updates)
            self._conn.execute(f"UPDATE messages SET {sets} WHERE id = ?", (*updates.values(), message_id))  # noqa: S608
            row = self._conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
            return MessageRecord.from_row(row) if row else None

    def list_non_terminal_reply_messages(self) -> list[MessageRecord]:
        """Post: every non-user message still pending/streaming, by thread then seq (#1491)."""
        self._ensure_available()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages "
                "WHERE delivery IN ('pending', 'streaming') "
                "AND author_kind != 'user' "
                "ORDER BY thread_id, seq ASC"
            ).fetchall()
        return [MessageRecord.from_row(r) for r in rows]

    # ── ACTION PROPOSALS ─────────────────────────────────────────────────────

    def create_proposal(
        self,
        message_id: str,
        thread_id: str = "",
        action: str = "",
        params: dict[str, Any] | None = None,
        risk: str = "low",
        proposal_id: str | None = None,
        principal: str = "",
    ) -> ActionProposalRecord:
        self._ensure_available()
        return _proposals.create_proposal(
            self._conn,
            self._lock,
            message_id=message_id,
            thread_id=thread_id,
            action=action,
            params=params,
            risk=risk,
            proposal_id=proposal_id,
            principal=principal,
            audit_store=self._audit_store,
        )

    def get_proposal(self, proposal_id: str) -> ActionProposalRecord | None:
        self._ensure_available()
        return _proposals.get_proposal(self._conn, self._lock, proposal_id)

    def list_proposals(
        self,
        thread_id: str | None = None,
        message_id: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[ActionProposalRecord]:
        self._ensure_available()
        return _proposals.list_proposals(
            self._conn,
            self._lock,
            thread_id=thread_id,
            message_id=message_id,
            state=state,
            limit=limit,
        )

    def decide_proposal(
        self,
        proposal_id: str,
        state: str,
        decided_by: str,
        reason: str = "",
        audit_store: StaffAuditStore | None = None,
    ) -> ActionProposalRecord:
        self._ensure_available()
        return _proposals.decide_proposal(
            self._conn,
            self._lock,
            proposal_id=proposal_id,
            state=state,
            decided_by=decided_by,
            reason=reason,
            audit_store=audit_store or self._audit_store,
        )

    def transition_proposal_state(
        self,
        proposal_id: str,
        new_state: str,
        decided_by: str = "",
        reason: str = "",
        audit_store: StaffAuditStore | None = None,
    ) -> ActionProposalRecord:
        self._ensure_available()
        return _proposals.transition_proposal_state(
            self._conn,
            self._lock,
            proposal_id=proposal_id,
            new_state=new_state,
            decided_by=decided_by,
            reason=reason,
            audit_store=audit_store or self._audit_store,
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
            self._audit_store.close()


_conversation_store: ConversationStore | None = None
_conversation_store_lock = threading.Lock()


def get_conversation_store(path: Path | None = None) -> ConversationStore:
    global _conversation_store  # noqa: PLW0603
    target_path = path or default_db_path()
    with _conversation_store_lock:
        if _conversation_store is None or _conversation_store.path != target_path:
            _conversation_store = ConversationStore(path=target_path)
        return _conversation_store


def get_conversation_store_status() -> ConversationStoreStatus:
    try:
        return get_conversation_store().status
    except Exception as exc:
        return ConversationStoreStatus(
            available=False,
            error=str(exc),
            banner_message=f"Conversations disabled: {exc}",
        )


def reset_conversation_store() -> None:
    global _conversation_store  # noqa: PLW0603
    with _conversation_store_lock:
        if _conversation_store is not None:
            _conversation_store.close()
        _conversation_store = None
