"""SQLite-backed principal registry and inbox for the agent coordination bus (#2155)."""

from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from agent_bus import store_ops
from agent_bus.models import MAX_BODY, SendBody, normalize_to_address
from agent_bus.store_rows import PrincipalRow, iso_after, message_dict
from agent_bus.store_threads import ensure_work_thread
from identity import Principal, identity_manager
from staff.conversations import get_conversation_store
from staff.store import _now, default_db_path, first_touch_lock
from time_utils import utc_now_iso

log = logging.getLogger("dashboard.agent_bus")

_store: AgentBusStore | None = None


def get_store() -> AgentBusStore:
    global _store
    if _store is None:
        _store = AgentBusStore()
    return _store


def reset_store() -> None:
    global _store
    _store = None


class AgentBusStore:
    def __init__(self, db_path: Any | None = None) -> None:
        self.db_path = db_path or default_db_path()
        self._lock = threading.RLock()
        with first_touch_lock(self.db_path):
            conn = self._conn()
            conn.execute(
                "CREATE TABLE IF NOT EXISTS agent_bus_principals ("
                "session_id TEXT PRIMARY KEY, role TEXT NOT NULL, provider TEXT NOT NULL, "
                "node TEXT NOT NULL, principal_id TEXT NOT NULL, lease_expires_at TEXT NOT NULL, "
                "registered_at TEXT NOT NULL, last_heartbeat_at TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS agent_bus_inbox ("
                "cursor INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE, "
                "idempotency_key TEXT NOT NULL, from_session TEXT NOT NULL, to_address TEXT NOT NULL, "
                "msg_type TEXT NOT NULL, repo TEXT NOT NULL, work_kind TEXT NOT NULL, work_number INTEGER NOT NULL, "
                "body_md TEXT NOT NULL, thread_id TEXT NOT NULL, thread_message_id TEXT NOT NULL, "
                "created_at TEXT NOT NULL, acked_at TEXT, "
                "UNIQUE(from_session, idempotency_key))"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS agent_bus_inbox_to ON agent_bus_inbox(to_address, cursor)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS agent_bus_claims ("
                "session_id TEXT NOT NULL, repo TEXT NOT NULL, issue INTEGER NOT NULL, "
                "role TEXT NOT NULL, claimed_at TEXT NOT NULL, "
                "PRIMARY KEY (session_id, repo, issue))"
            )
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def get_principal(self, session_id: str) -> PrincipalRow | None:
        with self._lock:
            row = (
                self._conn()
                .execute("SELECT * FROM agent_bus_principals WHERE session_id = ?", (session_id,))
                .fetchone()
            )
        return self._row_principal(row) if row else None

    def _row_principal(self, row: sqlite3.Row) -> PrincipalRow:
        return PrincipalRow(
            session_id=row["session_id"],
            role=row["role"],
            provider=row["provider"],
            node=row["node"],
            principal_id=row["principal_id"],
            lease_expires_at=row["lease_expires_at"],
        )

    def register(
        self,
        *,
        session_id: str,
        role: str,
        provider: str,
        node: str,
        principal: Principal,
        lease_seconds: int,
        mint_token: bool,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        lease_expires = iso_after(lease_seconds)
        principal_id = principal.id
        token: str | None = None
        if mint_token:
            principal_id = f"fleet-session:{session_id}"
            session_principal = Principal(
                id=principal_id,
                type="bot",
                name=f"{role}@{node}",
                roles=["bot"],
            )
            raw = f"abs_{secrets.token_urlsafe(32)}"
            identity_manager.add_ephemeral_token(
                hashlib.sha256(raw.encode()).hexdigest(),
                session_principal,
                expires_at=datetime.now(UTC).timestamp() + lease_seconds,
                name=f"agent-bus-{session_id}",
            )
            token = raw
        with self._lock:
            conn = self._conn()
            conn.execute(
                "INSERT INTO agent_bus_principals "
                "(session_id, role, provider, node, principal_id, lease_expires_at, registered_at, last_heartbeat_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET role=excluded.role, provider=excluded.provider, "
                "node=excluded.node, principal_id=excluded.principal_id, lease_expires_at=excluded.lease_expires_at, "
                "last_heartbeat_at=excluded.last_heartbeat_at",
                (session_id, role, provider, node, principal_id, lease_expires, now, now),
            )
            conn.close()
        out: dict[str, Any] = {
            "session": session_id,
            "role": role,
            "provider": provider,
            "node": node,
            "principal_id": principal_id,
            "lease_expires_at": lease_expires,
            "generated_at": utc_now_iso(),
        }
        if token:
            out["token"] = token
        log.info("agent_bus: registered session %s role=%s node=%s", session_id, role, node)
        return out

    def heartbeat(self, session_id: str, lease_seconds: int) -> dict[str, Any]:
        row = self.get_principal(session_id)
        if row is None:
            raise KeyError(session_id)
        lease_expires = iso_after(lease_seconds)
        now = utc_now_iso()
        with self._lock:
            conn = self._conn()
            conn.execute(
                "UPDATE agent_bus_principals SET lease_expires_at = ?, last_heartbeat_at = ? WHERE session_id = ?",
                (lease_expires, now, session_id),
            )
            conn.close()
        return {"session": session_id, "lease_expires_at": lease_expires, "generated_at": utc_now_iso()}

    def require_live(self, session_id: str) -> PrincipalRow:
        row = self.get_principal(session_id)
        if row is None or not row.live():
            raise RuntimeError(f"session {session_id!r} is not registered or lease expired")
        return row

    @staticmethod
    def _find_by_key(conn: sqlite3.Connection, body: SendBody) -> sqlite3.Row | None:
        row: sqlite3.Row | None = conn.execute(
            "SELECT * FROM agent_bus_inbox WHERE from_session = ? AND idempotency_key = ?",
            (body.session, body.idempotency_key),
        ).fetchone()
        return row

    def send(self, body: SendBody) -> dict[str, Any]:
        if len(body.body.encode("utf-8")) > MAX_BODY:
            raise ValueError("message body exceeds size cap")
        sender = self.require_live(body.session)
        to_address = normalize_to_address(body.to)
        # One critical section for SELECT -> thread write -> INSERT (#3024) so a
        # concurrent duplicate cannot slip between the replay check and the insert.
        with self._lock:
            conn = self._conn()
            try:
                existing = self._find_by_key(conn, body)
                if existing is not None:
                    return {"message": message_dict(existing), "replayed": True}

                work_number = body.work_number
                work_kind = body.work_kind
                assert work_number is not None and work_kind is not None  # noqa: S101
                cstore = get_conversation_store()
                thread_id = ensure_work_thread(cstore, body.repo, work_number, work_kind)
                msg_kind = body.type if body.type in ("handoff", "status") else "text"
                meta = {
                    "agent_bus": True,
                    "type": body.type,
                    "repo": body.repo,
                    "work_kind": work_kind,
                    "work_number": work_number,
                    "to": to_address,
                    "from_session": body.session,
                    "from_role": sender.role,
                }
                thread_msg = cstore.add_message(
                    thread_id=thread_id,
                    author_kind="role",
                    author=sender.role,
                    kind=msg_kind,
                    body_md=body.body,
                    meta=meta,
                    idempotency_key=body.idempotency_key,
                )
                message_id = f"abm_{uuid.uuid4().hex[:12]}"
                now = _now()
                try:
                    conn.execute(
                        "INSERT INTO agent_bus_inbox "
                        "(message_id, idempotency_key, from_session, to_address, msg_type, repo, work_kind, "
                        "work_number, body_md, thread_id, thread_message_id, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            message_id,
                            body.idempotency_key,
                            body.session,
                            to_address,
                            body.type,
                            body.repo,
                            body.work_kind,
                            body.work_number,
                            body.body,
                            thread_id,
                            thread_msg.id,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError:
                    # Another connection/process won UNIQUE(from_session, idempotency_key): replay its row.
                    existing = self._find_by_key(conn, body)
                    if existing is None:
                        raise
                    return {"message": message_dict(existing), "replayed": True}
                row = conn.execute("SELECT * FROM agent_bus_inbox WHERE message_id = ?", (message_id,)).fetchone()
            finally:
                conn.close()
        log.info(
            "agent_bus: message %s %s -> %s %s#%s",
            message_id,
            body.session,
            to_address,
            body.repo,
            body.work_number,
        )
        out = {"message": message_dict(row), "replayed": False}
        from agent_bus.store_rows import BARB_ROLE  # noqa: PLC0415

        if body.type == "blocked" and to_address != BARB_ROLE:
            store_ops.mirror_blocked_to_barb(self, body, sender)
        return out

    def list_inbox(self, session_id: str, since_cursor: int, limit: int) -> dict[str, Any]:
        row = self.get_principal(session_id)
        if row is None:
            raise KeyError(session_id)
        addresses = {session_id, row.role}
        placeholders = ",".join("?" for _ in addresses)
        with self._lock:
            rows = (
                self._conn()
                .execute(
                    f"SELECT * FROM agent_bus_inbox WHERE to_address IN ({placeholders}) "
                    "AND cursor > ? ORDER BY cursor ASC LIMIT ?",
                    (*addresses, since_cursor, limit),
                )
                .fetchall()
            )
        messages = [message_dict(r) for r in rows]
        next_cursor = messages[-1]["cursor"] if messages else since_cursor
        return {
            "session": session_id,
            "messages": messages,
            "next_cursor": next_cursor,
            "generated_at": utc_now_iso(),
        }

    def ack(self, session_id: str, message_id: str) -> dict[str, Any]:
        row = self.get_principal(session_id)
        if row is None:
            raise KeyError(session_id)
        addresses = {session_id, row.role}
        now = _now()
        with self._lock:
            conn = self._conn()
            msg = conn.execute("SELECT * FROM agent_bus_inbox WHERE message_id = ?", (message_id,)).fetchone()
            if msg is None or msg["to_address"] not in addresses:
                conn.close()
                raise LookupError(message_id)
            conn.execute(
                "UPDATE agent_bus_inbox SET acked_at = ? WHERE message_id = ? AND acked_at IS NULL",
                (now, message_id),
            )
            conn.close()
        return {"message_id": message_id, "acked": True, "generated_at": utc_now_iso()}

    def overview(self, inbox_limit: int = 8, activity_limit: int = 40) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self._lock:
            principal_rows = self._conn().execute("SELECT * FROM agent_bus_principals").fetchall()
        agents: list[dict[str, Any]] = []
        for raw in principal_rows:
            row = self._row_principal(raw)
            if not row.live(now):
                continue
            preview = self.list_inbox(row.session_id, 0, inbox_limit)
            agents.append(
                {
                    "session": row.session_id,
                    "role": row.role,
                    "provider": row.provider,
                    "node": row.node,
                    "lease_expires_at": row.lease_expires_at,
                    "inbox_preview": preview["messages"],
                }
            )
        with self._lock:
            activity_rows = (
                self._conn()
                .execute(
                    "SELECT * FROM agent_bus_inbox ORDER BY cursor DESC LIMIT ?",
                    (activity_limit,),
                )
                .fetchall()
            )
        activity = [message_dict(r) for r in activity_rows]
        threads: dict[str, dict[str, Any]] = {}
        for msg in activity:
            tid = str(msg["thread_id"])
            if tid not in threads:
                threads[tid] = {
                    "thread_id": tid,
                    "repo": msg["repo"],
                    "work_kind": msg["work_kind"],
                    "work_number": msg["work_number"],
                    "last_message": msg,
                }
        backlog = self.build_backlog()
        return {
            "agents": agents,
            "threads": list(threads.values()),
            "activity": activity,
            "backlog": {
                "stale_sessions": backlog["stale_sessions"],
                "blocked": backlog["blocked"],
            },
            "generated_at": utc_now_iso(),
        }

    def expire_session(self, session_id: str) -> None:
        store_ops.expire_session(self, session_id)

    def list_stale_principals(self) -> list[PrincipalRow]:
        return store_ops.list_stale_principals(self)

    def drop_principal(self, session_id: str) -> None:
        store_ops.drop_principal(self, session_id)

    def track_claim(self, session_id: str, role: str, repo: str, issue: int) -> dict[str, Any]:
        return store_ops.track_claim(self, session_id, role, repo, issue)

    def release_tracked_claims(self, session_id: str, role: str, *, reason: str) -> int:
        return store_ops.release_tracked_claims(self, session_id, role, reason=reason)

    def list_inbox_for_address(self, to_address: str, since_cursor: int, limit: int) -> dict[str, Any]:
        return store_ops.list_inbox_for_address(self, to_address, since_cursor, limit)

    def build_backlog(self) -> dict[str, Any]:
        return store_ops.build_backlog(self)

    def upsert_handoff(
        self,
        *,
        session_id: str,
        repo: str,
        work_kind: str,
        work_number: int,
        body: dict[str, str],
    ) -> dict[str, Any]:
        return store_ops.upsert_handoff(
            self,
            session_id=session_id,
            repo=repo,
            work_kind=work_kind,
            work_number=work_number,
            body=body,
        )

    def mirror_blocked_to_barb(self, body: SendBody, sender: PrincipalRow) -> None:
        store_ops.mirror_blocked_to_barb(self, body, sender)
