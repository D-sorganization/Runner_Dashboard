"""SQLite-backed principal registry and inbox for the agent coordination bus (#2155)."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_bus.models import MAX_BODY, SendBody, normalize_to_address
from identity import Principal, identity_manager
from staff.conversations import ConversationStore, get_conversation_store
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


def _iso_after(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class PrincipalRow:
    session_id: str
    role: str
    provider: str
    node: str
    principal_id: str
    lease_expires_at: str

    def live(self, now: datetime | None = None) -> bool:
        when = now or datetime.now(UTC)
        try:
            exp = datetime.fromisoformat(self.lease_expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        return exp > when


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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS agent_bus_inbox_to ON agent_bus_inbox(to_address, cursor)"
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
            row = self._conn().execute(
                "SELECT * FROM agent_bus_principals WHERE session_id = ?", (session_id,)
            ).fetchone()
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
        lease_expires = _iso_after(lease_seconds)
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
        lease_expires = _iso_after(lease_seconds)
        now = utc_now_iso()
        with self._lock:
            conn = self._conn()
            conn.execute(
                "UPDATE agent_bus_principals SET lease_expires_at = ?, last_heartbeat_at = ? WHERE session_id = ?",
                (lease_expires, now, session_id),
            )
            conn.close()
        return {"session": session_id, "lease_expires_at": lease_expires, "generated_at": utc_now_iso()}

    def expire_session(self, session_id: str) -> None:
        past = (datetime.now(UTC) - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
        with self._lock:
            conn = self._conn()
            conn.execute(
                "UPDATE agent_bus_principals SET lease_expires_at = ? WHERE session_id = ?",
                (past, session_id),
            )
            conn.close()

    def require_live(self, session_id: str) -> PrincipalRow:
        row = self.get_principal(session_id)
        if row is None or not row.live():
            raise RuntimeError(f"session {session_id!r} is not registered or lease expired")
        return row

    def _work_thread_id(self, repo: str, number: int) -> str:
        safe = repo.replace("/", "-")
        return f"wi-{safe}-{number}"

    def _ensure_work_thread(self, cstore: ConversationStore, repo: str, number: int, kind: str) -> str:
        tid = self._work_thread_id(repo, number)
        if cstore.get_thread(tid):
            return tid
        title = f"{repo}#{number} ({kind})"
        meta = {"agent_bus_work_item": True, "repo": repo, "number": number, "work_kind": kind}
        cstore.create_thread(
            title=title,
            kind="auto",
            participants=["barb"],
            created_by="agent-bus",
            thread_id=tid,
            meta=meta,
        )
        return tid

    def send(self, body: SendBody) -> dict[str, Any]:
        if len(body.body.encode("utf-8")) > MAX_BODY:
            raise ValueError("message body exceeds size cap")
        sender = self.require_live(body.session)
        to_address = normalize_to_address(body.to)
        with self._lock:
            conn = self._conn()
            existing = conn.execute(
                "SELECT * FROM agent_bus_inbox WHERE from_session = ? AND idempotency_key = ?",
                (body.session, body.idempotency_key),
            ).fetchone()
            if existing:
                conn.close()
                return {"message": self._message_dict(existing), "replayed": True}
            conn.close()

        cstore = get_conversation_store()
        thread_id = self._ensure_work_thread(cstore, body.repo, body.work_number, body.work_kind)
        msg_kind = body.type if body.type in ("handoff", "status") else "text"
        meta = {
            "agent_bus": True,
            "type": body.type,
            "repo": body.repo,
            "work_kind": body.work_kind,
            "work_number": body.work_number,
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
        with self._lock:
            conn = self._conn()
            conn.execute(
                "INSERT INTO agent_bus_inbox "
                "(message_id, idempotency_key, from_session, to_address, msg_type, repo, work_kind, work_number, "
                "body_md, thread_id, thread_message_id, created_at) "
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
            row = conn.execute("SELECT * FROM agent_bus_inbox WHERE message_id = ?", (message_id,)).fetchone()
            conn.close()
        log.info(
            "agent_bus: message %s %s -> %s %s#%s",
            message_id,
            body.session,
            to_address,
            body.repo,
            body.work_number,
        )
        return {"message": self._message_dict(row), "replayed": False}

    def list_inbox(self, session_id: str, since_cursor: int, limit: int) -> dict[str, Any]:
        row = self.get_principal(session_id)
        if row is None:
            raise KeyError(session_id)
        addresses = {session_id, row.role}
        placeholders = ",".join("?" for _ in addresses)
        with self._lock:
            rows = self._conn().execute(
                f"SELECT * FROM agent_bus_inbox WHERE to_address IN ({placeholders}) "
                "AND cursor > ? ORDER BY cursor ASC LIMIT ?",
                (*addresses, since_cursor, limit),
            ).fetchall()
        messages = [self._message_dict(r) for r in rows]
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

    @staticmethod
    def _message_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["message_id"],
            "cursor": row["cursor"],
            "from_session": row["from_session"],
            "to": row["to_address"],
            "type": row["msg_type"],
            "repo": row["repo"],
            "work_kind": row["work_kind"],
            "work_number": row["work_number"],
            "body": row["body_md"],
            "thread_id": row["thread_id"],
            "thread_message_id": row["thread_message_id"],
            "created_at": row["created_at"],
            "acked_at": row["acked_at"],
        }
