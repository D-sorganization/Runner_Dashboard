"""Data models and constants for staff conversations (SC-B2, Issue #1305)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from staff.store import _now

THREAD_KINDS = ("direct", "group", "auto")
THREAD_STATUSES = ("open", "archived")
MESSAGE_AUTHOR_KINDS = ("user", "role", "system")
MESSAGE_KINDS = (
    "text",
    "action_proposal",
    "action_result",
    "run_card",
    "handoff",
    "status",
    "error",
)
MESSAGE_DELIVERIES = ("pending", "streaming", "complete", "failed")
PROPOSAL_STATES = (
    "proposed",
    "approved",
    "denied",
    "executing",
    "done",
    "failed",
    "expired",
)
PROPOSAL_RISKS = ("read", "low", "medium", "high", "critical", "owner-only")

_VALID_PROPOSAL_TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"approved", "denied", "expired", "executing", "failed"},
    "approved": {"executing", "failed", "expired"},
    "denied": set(),
    "executing": {"done", "failed"},
    "done": set(),
    "failed": {"executing"},
    "expired": set(),
}


class ConversationsUnavailableError(RuntimeError):
    """Raised when conversation operations are invoked while the store is degraded."""


@dataclass
class ConversationStoreStatus:
    """Readiness and migration status for the conversation store."""

    available: bool = True
    error: str = ""
    banner_message: str = ""
    migration_version: int = 0


@dataclass
class ThreadRecord:
    """Flat row for one conversation thread."""

    id: str
    title: str
    kind: str = "direct"
    participants: list[str] = field(default_factory=list)
    created_by: str = ""
    status: str = "open"
    last_message_at: str | None = None
    unread_counters: dict[str, int] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "participants": list(self.participants),
            "created_by": self.created_by,
            "status": self.status,
            "last_message_at": self.last_message_at,
            "unread_counters": dict(self.unread_counters),
            "meta": dict(self.meta),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> ThreadRecord:
        d = dict(row)
        raw_parts = d.get("participants")
        parts = json.loads(raw_parts) if isinstance(raw_parts, str) else (raw_parts or [])
        raw_unread = d.get("unread_counters")
        unread = json.loads(raw_unread) if isinstance(raw_unread, str) else (raw_unread or {})
        raw_meta = d.get("meta")
        meta = json.loads(raw_meta) if isinstance(raw_meta, str) else (raw_meta or {})
        return cls(
            id=str(d["id"]),
            title=str(d["title"]),
            kind=str(d["kind"]),
            participants=list(parts),
            created_by=str(d.get("created_by") or ""),
            status=str(d.get("status") or "open"),
            last_message_at=d.get("last_message_at"),
            unread_counters=dict(unread),
            meta=dict(meta),
            created_at=str(d["created_at"]),
            updated_at=str(d["updated_at"]),
        )


@dataclass
class MessageRecord:
    """Flat row for one message within a conversation thread."""

    id: str
    thread_id: str
    seq: int
    author_kind: str = "user"
    author: str = ""
    kind: str = "text"
    body_md: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    idempotency_key: str | None = None
    created_at: str = field(default_factory=_now)
    delivery: str = "complete"

    @property
    def failure_class(self) -> str | None:
        return self.meta.get("failure_class")

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id,
            "thread_id": self.thread_id,
            "seq": self.seq,
            "author_kind": self.author_kind,
            "author": self.author,
            "kind": self.kind,
            "body_md": self.body_md,
            "meta": dict(self.meta),
            "run_id": self.run_id,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
            "delivery": self.delivery,
        }
        if "failure_class" in self.meta:
            d["failure_class"] = self.meta["failure_class"]
        return d

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> MessageRecord:
        d = dict(row)
        raw_meta = d.get("meta")
        meta = json.loads(raw_meta) if isinstance(raw_meta, str) else (raw_meta or {})
        return cls(
            id=str(d["id"]),
            thread_id=str(d["thread_id"]),
            seq=int(d["seq"]),
            author_kind=str(d.get("author_kind") or "user"),
            author=str(d.get("author") or ""),
            kind=str(d.get("kind") or "text"),
            body_md=str(d.get("body_md") or ""),
            meta=dict(meta),
            run_id=str(d.get("run_id") or ""),
            idempotency_key=d.get("idempotency_key"),
            created_at=str(d["created_at"]),
            delivery=str(d.get("delivery") or "complete"),
        )


@dataclass
class ActionProposalRecord:
    """Flat row for an actionable proposal originating from a conversation."""

    id: str
    message_id: str
    thread_id: str = ""
    action: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    risk: str = "low"
    state: str = "proposed"
    decided_by: str = ""
    decided_at: str | None = None
    reason: str = ""
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "thread_id": self.thread_id,
            "action": self.action,
            "params": dict(self.params),
            "risk": self.risk,
            "state": self.state,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "reason": self.reason,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> ActionProposalRecord:
        d = dict(row)
        raw_params = d.get("params")
        params = json.loads(raw_params) if isinstance(raw_params, str) else (raw_params or {})
        return cls(
            id=str(d["id"]),
            message_id=str(d["message_id"]),
            thread_id=str(d.get("thread_id") or ""),
            action=str(d["action"]),
            params=dict(params),
            risk=str(d.get("risk") or "low"),
            state=str(d.get("state") or "proposed"),
            decided_by=str(d.get("decided_by") or ""),
            decided_at=d.get("decided_at"),
            reason=str(d.get("reason") or ""),
            created_at=str(d["created_at"]),
        )


class CreateThreadRequest(BaseModel):
    title: str | None = None
    kind: str = "direct"
    role: str | None = None
    participants: list[str] = Field(default_factory=list)


class UpdateThreadRequest(BaseModel):
    title: str | None = None
    status: str | None = None


class PostMessageRequest(BaseModel):
    body: str
    kind: str = "text"
    meta: dict[str, Any] = Field(default_factory=dict)


class AnswerNeedsInputRequest(BaseModel):
    answer: str
