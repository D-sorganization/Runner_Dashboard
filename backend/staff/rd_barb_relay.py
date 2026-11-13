"""rd-barb slice 7 (#3075): board notifications, SQLite relay retry queue, 5-min degraded status, and answer routing."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from staff.conversation_models import MessageRecord
from staff.conversations import ConversationStore
from staff.rd_barb_inbox import DashboardInbox
from staff.rd_barb_ledger import DECISIONS, Ask, DecisionLedger, is_locked
from staff.rd_barb_memory import rd_barb_state_dir
from staff.rd_barb_notify_health import NotifyHealth
from staff.rd_barb_policy import LockedPolicy, load_locked_policy

log = logging.getLogger("dashboard.staff.rd_barb_relay")

BACKOFF_BASE_S = 5.0
BACKOFF_CAP_S = 300.0
RELAY_KINDS = frozenset({"question", "alert"})
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS relay_queue ("
    "dedupe_key TEXT PRIMARY KEY, envelope_json TEXT NOT NULL, status TEXT NOT NULL, "
    "attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0, "
    "created_at REAL NOT NULL, last_error TEXT NOT NULL DEFAULT '')"
)


class CorrelationRejectedError(ValueError):
    """Answer carries an unknown, non-pending, or expired correlation_id (#3075)."""


def default_relay_queue_path() -> Path:
    return rd_barb_state_dir() / "relay_queue.sqlite3"


def backoff_seconds(attempts: int) -> float:
    return float(min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** max(0, attempts - 1))))


def ingest_board_post(post: Mapping[str, Any], inbox: DashboardInbox, *, source: str = "fleet-board") -> dict[str, Any]:
    """Convert a fleet board (to='*') or presence board post into one deduped ``update`` in rd-barb's inbox (#3075)."""
    post_id = str(post.get("id") or post.get("dedupe_key") or "").strip()
    if not post_id:
        raise ValueError("board post must carry a non-empty id or dedupe_key")
    key = f"board:{post_id}"
    before = {i["id"] for i in inbox.unacknowledged()}
    summary = str(post.get("summary") or f"[{source}] board update")[:200]
    body = str(post.get("body") or post.get("text") or "")
    inbox.add(
        {
            "kind": "update",
            "urgency": str(post.get("urgency") or "low"),
            "summary": summary,
            "body": body,
            "dedupe_key": key,
        }
    )
    status = "added" if {i["id"] for i in inbox.unacknowledged()} != before else "deduped"
    return {"ok": True, "status": status, "dedupe_key": key}


class RelayRetryQueue:
    """Durable SQLite retry queue for Grok webhook questions and alerts with 5-minute degraded state (#3075)."""

    def __init__(self, path: Path | None = None, *, clock: Callable[[], float] = time.time) -> None:
        self.path = path or default_relay_queue_path()
        self._clock, self._lock = clock, threading.Lock()
        self._health = NotifyHealth(self.path.with_suffix(".health.json"))
        with self._lock, self._connect() as conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def relay(self, envelope: Mapping[str, Any], sender: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
        """Attempt immediate Grok webhook delivery or enqueue for backoff retry; never double-relays a dedupe_key."""
        if str(envelope.get("channel") or envelope.get("transport") or "grok_webhook").strip().lower() == "sms":
            return {"ok": False, "status": "rejected", "error": "sms_disallowed"}
        kind, key = str(envelope.get("kind") or ""), str(envelope.get("dedupe_key") or "").strip()
        if kind not in RELAY_KINDS or not key:
            return {"ok": False, "status": "rejected", "error": "invalid_envelope"}
        now, payload = self._clock(), dict(envelope)
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT status FROM relay_queue WHERE dedupe_key = ?", (key,)).fetchone()
            if row is not None:
                return {"ok": row["status"] == "sent", "status": "deduped" if row["status"] == "sent" else "queued"}
        ok, err = self._try_send(sender, payload)
        with self._lock, self._connect() as conn:
            next_at = now if ok else now + backoff_seconds(1)
            sql = "INSERT OR REPLACE INTO relay_queue VALUES (?, ?, ?, 1, ?, ?, ?)"
            conn.execute(sql, (key, json.dumps(payload, sort_keys=True), "sent" if ok else "queued", next_at, now, err))
        self._health.record(ok=ok, channel="grok_webhook", now=now, error=err)
        return {"ok": ok, "status": "sent" if ok else "queued", "dedupe_key": key}

    def flush_due(self, sender: Callable[[dict[str, Any]], Any]) -> dict[str, int]:
        """Retry due queued envelopes in FIFO order using exponential backoff."""
        now, sent = self._clock(), 0
        with self._lock, self._connect() as conn:
            sql = "SELECT * FROM relay_queue WHERE status = 'queued' AND next_attempt_at <= ? ORDER BY created_at ASC"
            due = conn.execute(sql, (now,)).fetchall()
        for row in due:
            ok, err = self._try_send(sender, json.loads(row["envelope_json"]))
            with self._lock, self._connect() as conn:
                if ok:
                    conn.execute(
                        "UPDATE relay_queue SET status = 'sent', last_error = '' WHERE dedupe_key = ?",
                        (row["dedupe_key"],),
                    )
                    sent += 1
                else:
                    att = int(row["attempts"]) + 1
                    upd = (
                        "UPDATE relay_queue SET attempts = ?, next_attempt_at = ?, last_error = ? WHERE dedupe_key = ?"
                    )
                    conn.execute(upd, (att, now + backoff_seconds(att), err, row["dedupe_key"]))
            self._health.record(ok=ok, channel="grok_webhook", now=now, error=err)
        with self._lock, self._connect() as conn:
            rem = conn.execute("SELECT COUNT(*) AS n FROM relay_queue WHERE status = 'queued'").fetchone()
        return {"sent": sent, "remaining": int(rem["n"]) if rem else 0}

    def status(self, *, now: float | None = None) -> dict[str, Any]:
        """Relay health status; ``state`` becomes ``degraded`` and ``banner_visible`` True at 5 minutes of failure."""
        at = self._clock() if now is None else now
        out = self._health.status(now=at)
        with self._lock, self._connect() as conn:
            q = conn.execute("SELECT COUNT(*) AS n FROM relay_queue WHERE status = 'queued'").fetchone()
        deg = out["state"] == "degraded"
        return {**out, "degraded": deg, "banner_visible": deg, "queued_count": int(q["n"]) if q else 0}

    @staticmethod
    def _try_send(sender: Callable[[dict[str, Any]], Any], payload: dict[str, Any]) -> tuple[bool, str]:
        try:
            res = sender(payload)
            if isinstance(res, Mapping) and res.get("ok") is False:
                return False, str(res.get("error") or "webhook_failed")
            return True, ""
        except Exception as exc:  # noqa: BLE001 - fail closed into durable queue
            return False, f"{type(exc).__name__}:{exc}"[:160]


def register_question(
    store: ConversationStore,
    *,
    thread_id: str,
    correlation_id: str,
    summary: str,
    ask: Ask,
    expiry: datetime | None = None,
) -> MessageRecord:
    """Persist an ``input-required`` question in an rd-barb thread keyed by ``correlation_id`` (#3075)."""
    if not correlation_id.strip():
        raise ValueError("correlation_id must be non-empty")
    meta: dict[str, Any] = {
        "correlation_id": correlation_id.strip(),
        "state": "input-required",
        "ask": {"action": ask.action, "repo": ask.repo, "scope": ask.scope},
    }
    if expiry is not None:
        meta["expiry"] = expiry.isoformat()
    return store.add_message(thread_id, author_kind="role", author="rd-barb", kind="text", body_md=summary, meta=meta)


def route_answer(
    store: ConversationStore,
    ledger: DecisionLedger,
    *,
    correlation_id: str,
    decision: str,
    body: str = "",
    author: str = "grok-barb",
    now: datetime | None = None,
    policy: LockedPolicy | None = None,
) -> dict[str, Any]:
    """Route Dieter's correlated answer to its rd-barb thread, mark completed, and write a #3066 ledger record."""
    if decision not in DECISIONS:
        raise ValueError("decision must be 'approve' or 'deny'")
    at, pol, cid = now or datetime.now(UTC), policy or load_locked_policy(), correlation_id.strip()
    hit = next(
        (
            (th.id, m)
            for th in store.list_threads(limit=250)
            for m in store.list_messages(th.id, limit=250)
            if m.meta.get("correlation_id") == cid and m.meta.get("state") == "input-required"
        ),
        None,
    )
    if hit is None:
        log.warning("rd-barb answer rejected: unknown correlation_id=%r", cid)
        raise CorrelationRejectedError(f"unknown_correlation:{cid}")
    tid, msg = hit
    exp_raw = msg.meta.get("expiry")
    if isinstance(exp_raw, str) and exp_raw and at >= datetime.fromisoformat(exp_raw):
        log.warning("rd-barb answer rejected: expired correlation_id=%r", cid)
        raise CorrelationRejectedError(f"expired_correlation:{cid}")
    raw_ask = msg.meta.get("ask") or {}
    ask = Ask(
        str(raw_ask.get("action") or "question"),
        str(raw_ask.get("repo") or "Runner_Dashboard"),
        str(raw_ask.get("scope") or cid),
    )
    store.update_message(
        msg.id, meta={**msg.meta, "state": "completed", "decision": decision, "answered_at": at.isoformat()}
    )
    reply_meta = {"correlation_id": cid, "decision": decision, "question_state": "completed"}
    reply = store.add_message(
        tid, author_kind="user", author=author, kind="text", body_md=body or f"Decision: {decision}", meta=reply_meta
    )
    rec = ledger.record_decision(ask, decision, source_thread_id=tid, correlation_id=cid)
    return {
        "ok": True,
        "thread_id": tid,
        "question_id": msg.id,
        "answer_message_id": reply.id,
        "state": "completed",
        "ledger_record_id": rec["id"],
        "touches_locked_hold": is_locked(ask.key, pol),
        "hold_lifted": False,
    }
