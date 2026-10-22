"""Stale-claim tracking, backlog surfacing, Barb escalation, handoff docs (#2155 slice 3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from agent_bus.handoff import next_handoff_document, validate_handoff_body
from agent_bus.models import SendBody
from agent_bus.store_rows import BARB_ROLE, PrincipalRow, message_dict
from agent_bus.store_threads import ensure_work_thread
from staff.conversations import get_conversation_store
from staff.store import _now
from time_utils import utc_now_iso

if TYPE_CHECKING:
    from agent_bus.store import AgentBusStore


def expire_session(store: AgentBusStore, session_id: str) -> None:
    past = (datetime.now(UTC) - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    with store._lock:
        conn = store._conn()
        conn.execute(
            "UPDATE agent_bus_principals SET lease_expires_at = ? WHERE session_id = ?",
            (past, session_id),
        )
        conn.close()


def list_stale_principals(store: AgentBusStore) -> list[PrincipalRow]:
    now = datetime.now(UTC)
    with store._lock:
        rows = store._conn().execute("SELECT * FROM agent_bus_principals").fetchall()
    return [store._row_principal(r) for r in rows if not store._row_principal(r).live(now)]


def drop_principal(store: AgentBusStore, session_id: str) -> None:
    with store._lock:
        conn = store._conn()
        conn.execute("DELETE FROM agent_bus_principals WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM agent_bus_claims WHERE session_id = ?", (session_id,))
        conn.close()


def track_claim(
    store: AgentBusStore, session_id: str, role: str, repo: str, issue: int
) -> dict[str, Any]:
    store.require_live(session_id)
    now = utc_now_iso()
    with store._lock:
        conn = store._conn()
        conn.execute(
            "INSERT INTO agent_bus_claims (session_id, repo, issue, role, claimed_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(session_id, repo, issue) DO UPDATE SET role=excluded.role, claimed_at=excluded.claimed_at",
            (session_id, repo, issue, role, now),
        )
        conn.close()
    return {"session": session_id, "repo": repo, "issue": issue, "tracked": True, "generated_at": now}


def release_tracked_claims(store: AgentBusStore, session_id: str, role: str, *, reason: str) -> int:
    from coordination import claims as coord_claims  # noqa: PLC0415

    with store._lock:
        rows = (
            store._conn()
            .execute("SELECT repo, issue FROM agent_bus_claims WHERE session_id = ?", (session_id,))
            .fetchall()
        )
    released = 0
    for row in rows:
        coord_claims.release(row["repo"], int(row["issue"]), agent=role, session=session_id, reason=reason)
        released += 1
    with store._lock:
        conn = store._conn()
        conn.execute("DELETE FROM agent_bus_claims WHERE session_id = ?", (session_id,))
        conn.close()
    return released


def list_inbox_for_address(
    store: AgentBusStore, to_address: str, since_cursor: int, limit: int
) -> dict[str, Any]:
    with store._lock:
        rows = (
            store._conn()
            .execute(
                "SELECT * FROM agent_bus_inbox WHERE to_address = ? AND cursor > ? ORDER BY cursor ASC LIMIT ?",
                (to_address, since_cursor, limit),
            )
            .fetchall()
        )
    messages = [message_dict(r) for r in rows]
    next_cursor = messages[-1]["cursor"] if messages else since_cursor
    return {"to": to_address, "messages": messages, "next_cursor": next_cursor, "generated_at": utc_now_iso()}


def build_backlog(store: AgentBusStore) -> dict[str, Any]:
    stale = [
        {
            "session": row.session_id,
            "role": row.role,
            "node": row.node,
            "lease_expires_at": row.lease_expires_at,
        }
        for row in list_stale_principals(store)
    ]
    barb = list_inbox_for_address(store, BARB_ROLE, since_cursor=0, limit=100)
    blocked = [m for m in barb["messages"] if m["type"] == "blocked" and not m.get("acked_at")]
    return {
        "stale_sessions": stale,
        "blocked": blocked,
        "generated_at": utc_now_iso(),
    }


def upsert_handoff(
    store: AgentBusStore,
    *,
    session_id: str,
    repo: str,
    work_kind: str,
    work_number: int,
    body: dict[str, str],
) -> dict[str, Any]:
    store.require_live(session_id)
    parsed = validate_handoff_body(body)
    cstore = get_conversation_store()
    thread_id = ensure_work_thread(cstore, repo, work_number, work_kind)
    thread = cstore.get_thread(thread_id)
    if thread is None:
        raise LookupError(thread_id)
    meta = dict(thread.meta)
    from agent_bus.work_item_thread import ensure_handoff_document_slot  # noqa: PLC0415

    meta = ensure_handoff_document_slot(meta)
    existing = meta.get("handoff_document") if isinstance(meta.get("handoff_document"), dict) else None
    doc = next_handoff_document(existing, parsed, updated_at=utc_now_iso())
    meta = {**meta, "handoff_document": doc}
    cstore.update_thread(thread_id, meta=meta)
    return {"thread_id": thread_id, "handoff_document": doc, "generated_at": utc_now_iso()}


def mirror_blocked_to_barb(store: AgentBusStore, body: SendBody, sender: PrincipalRow) -> None:
    barb_key = f"{body.idempotency_key}-barb"
    with store._lock:
        conn = store._conn()
        existing = conn.execute(
            "SELECT * FROM agent_bus_inbox WHERE from_session = ? AND idempotency_key = ?",
            (body.session, barb_key),
        ).fetchone()
        if existing:
            conn.close()
            return
        conn.close()
    work_number = body.work_number
    work_kind = body.work_kind
    assert work_number is not None and work_kind is not None  # noqa: S101
    cstore = get_conversation_store()
    thread_id = ensure_work_thread(cstore, body.repo, work_number, work_kind)
    meta = {
        "agent_bus": True,
        "type": "blocked",
        "repo": body.repo,
        "work_kind": work_kind,
        "work_number": work_number,
        "to": BARB_ROLE,
        "from_session": body.session,
        "from_role": sender.role,
    }
    cstore.add_message(
        thread_id=thread_id,
        author_kind="role",
        author=sender.role,
        kind="text",
        body_md=body.body,
        meta=meta,
        idempotency_key=barb_key,
    )
    message_id = f"abm_{uuid.uuid4().hex[:12]}"
    now = _now()
    with store._lock:
        conn = store._conn()
        conn.execute(
            "INSERT INTO agent_bus_inbox "
            "(message_id, idempotency_key, from_session, to_address, msg_type, repo, work_kind, work_number, "
            "body_md, thread_id, thread_message_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message_id,
                barb_key,
                body.session,
                BARB_ROLE,
                "blocked",
                body.repo,
                work_kind,
                work_number,
                body.body,
                thread_id,
                "",
                now,
            ),
        )
        conn.close()
