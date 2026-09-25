"""Helper routines for Staff Conversation & Thread API v1 (SC-B3 #1306)."""

from __future__ import annotations

import sqlite3
from typing import Any

from staff.conversations import ConversationStore, MessageRecord


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
