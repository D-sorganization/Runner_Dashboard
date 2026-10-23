"""Helper routines for Staff Conversation & Thread API v1 (SC-B3 #1306)."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from staff.conversation_models import MessageRecord

if TYPE_CHECKING:
    from staff.conversations import ConversationStore


def find_idempotent_reply(
    conn: sqlite3.Connection,
    thread_id: str,
    idempotency_key: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Find existing message and reply placeholder for an idempotent replay request."""
    existing = conn.execute(
        "SELECT * FROM messages WHERE thread_id = ? AND idempotency_key = ?",
        (thread_id, idempotency_key.strip()),
    ).fetchone()
    if not existing:
        return None

    user_msg = MessageRecord.from_row(existing)
    reply_row = conn.execute(
        "SELECT * FROM messages WHERE thread_id = ? AND seq = ?",
        (thread_id, user_msg.seq + 1),
    ).fetchone()

    reply_placeholder = (
        MessageRecord.from_row(reply_row).to_dict()
        if reply_row
        else {
            "id": f"pending_{user_msg.id}",
            "thread_id": thread_id,
            "author_kind": "role",
            "author": "barb",
            "kind": "text",
            "delivery": "pending",
        }
    )
    return user_msg.to_dict(), reply_placeholder


def collect_inbox_items(store: ConversationStore, caller_id: str) -> list[dict[str, Any]]:
    """Collect threads requiring caller attention (unread messages or pending proposals)."""
    open_threads = store.list_threads(status="open", limit=200)
    inbox_items: list[dict[str, Any]] = []

    for th in open_threads:
        has_unread = th.unread_counters.get(caller_id, 0) > 0
        proposals = store.list_proposals(thread_id=th.id, state="proposed", limit=10)
        has_proposals = len(proposals) > 0

        if has_unread or has_proposals:
            item = th.to_dict()
            item["pending_proposals_count"] = len(proposals)
            item["caller_unread_count"] = th.unread_counters.get(caller_id, 0)
            inbox_items.append(item)

    return inbox_items


def shape_new_thread(body: Any, caller_id: str) -> tuple[str, str, list[str], dict[str, Any]]:
    """Resolve title, kind, participants and meta for a new thread from a create request."""
    from staff.group_threads import resolve_group_thread_meta

    kind = body.kind or "direct"
    role = body.role
    if kind == "auto" or role == "auto":
        kind = "auto"
        role = "barb"

    participants = list(body.participants)
    kind, role, meta = resolve_group_thread_meta(kind, role, participants)

    if role and role not in participants:
        participants.append(role)
    if caller_id not in participants:
        participants.append(caller_id)

    title = body.title or (f"Conversation with {role.title()}" if role else "New conversation")
    return title, kind, participants, meta


class StaleThreadEpochError(ValueError):
    """A CLI turn attempted to write after its thread moved to a newer epoch."""


def advance_thread_turn_epoch(
    conn: sqlite3.Connection,
    lock: Any,
    thread_id: str,
    reason: str = "seat_handoff",
) -> int:
    """Atomically fence in-flight turns and return the new persisted thread epoch."""
    from staff.conversation_models import _now

    if not thread_id or not reason:
        raise ValueError("thread_id and reason are required to advance the turn epoch")
    with lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                "UPDATE threads SET epoch = epoch + 1, updated_at = ? WHERE id = ? RETURNING epoch",
                (_now(), thread_id),
            ).fetchone()
            if cur is None:
                raise KeyError(f"Unknown conversation thread: {thread_id}")
            epoch = int(cur["epoch"])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return epoch


def validate_turn_epoch(conn: sqlite3.Connection, thread_id: str, turn_epoch: int) -> None:
    """Ensure a writing turn has not been fenced by an epoch advance."""
    row = conn.execute("SELECT epoch FROM threads WHERE id = ?", (thread_id,)).fetchone()
    if row is None:
        raise KeyError(f"Unknown conversation thread: {thread_id}")
    if int(row["epoch"]) != turn_epoch:
        raise StaleThreadEpochError(f"thread {thread_id} is at epoch {row['epoch']}, turn is {turn_epoch}")


def query_threads(
    conn: sqlite3.Connection,
    lock: Any,
    kind: str | None = None,
    status: str | None = None,
    participant: str | None = None,
    limit: int = 50,
) -> list[Any]:
    from staff.conversation_models import ThreadRecord

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
    with lock:
        rows = conn.execute(query, tuple(params)).fetchall()
    threads = [ThreadRecord.from_row(r) for r in rows]
    return [t for t in threads if participant in t.participants] if participant else threads


def mutate_thread(conn: sqlite3.Connection, lock: Any, cur: Any, thread_id: str, **fields: Any) -> Any:
    import json

    from staff.conversation_models import ThreadRecord, _now
    from staff.redaction import redact_value

    if not cur:
        return None
    allowed = {"title", "status", "participants", "unread_counters", "meta"}
    updates = {
        k: redact_value(json.dumps(v) if k in {"participants", "unread_counters", "meta"} else v)
        for k, v in fields.items()
        if k in allowed
    }
    if not updates:
        return cur
    updates["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in updates)
    with lock:
        conn.execute(f"UPDATE threads SET {sets} WHERE id = ?", (*updates.values(), thread_id))  # noqa: S608
        row = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
    return ThreadRecord.from_row(row) if row else None
