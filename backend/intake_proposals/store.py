"""SQLite store for /api/v1/proposals (email intake work proposals)."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from email_intake.dispatch_role import UnknownStaffRoleError, normalize_staff_role
from intake_proposals.models import IntakeProposal
from staff.store import _config_dir, first_touch_lock

_SCHEMA = """
CREATE TABLE IF NOT EXISTS intake_proposals (
  id TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  subject TEXT NOT NULL,
  body_plain TEXT NOT NULL,
  sender TEXT NOT NULL,
  message_id TEXT NOT NULL,
  dispatch_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  decided_by TEXT NOT NULL DEFAULT '',
  decision_reason TEXT NOT NULL DEFAULT '',
  owner_approval_ref TEXT NOT NULL DEFAULT ''
);
"""


class IntakeProposalStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with first_touch_lock(self.path):
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA)
            return conn

    def create_pending(
        self,
        *,
        subject: str,
        body_plain: str,
        sender: str,
        message_id: str,
        dispatch: dict[str, Any],
    ) -> IntakeProposal:
        role_raw = str(dispatch.get("role") or "").strip()
        try:
            dispatch = {**dispatch, "role": normalize_staff_role(role_raw)}
        except UnknownStaffRoleError:
            raise
        prop = IntakeProposal(
            id=f"ip_{uuid.uuid4().hex[:12]}",
            state="pending",
            subject=subject,
            body_plain=body_plain,
            sender=sender,
            message_id=message_id,
            dispatch=dispatch,
            created_at=datetime.now(UTC),
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO intake_proposals
                (id, state, subject, body_plain, sender, message_id, dispatch_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prop.id,
                    prop.state,
                    prop.subject,
                    prop.body_plain,
                    prop.sender,
                    prop.message_id,
                    json.dumps(dispatch),
                    prop.created_at.isoformat(),
                ),
            )
            conn.commit()
        return prop

    def get(self, proposal_id: str) -> IntakeProposal | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM intake_proposals WHERE id = ?", (proposal_id,)).fetchone()
        return self._row_to_prop(row) if row else None

    def list(self, *, state: str | None = None, limit: int = 50) -> list[IntakeProposal]:
        limit = max(1, min(limit, 200))
        with self._lock, self._connect() as conn:
            if state:
                rows = conn.execute(
                    "SELECT * FROM intake_proposals WHERE state = ? ORDER BY created_at DESC LIMIT ?",
                    (state, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM intake_proposals ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_prop(r) for r in rows]

    def decide(
        self,
        proposal_id: str,
        *,
        decision: str,
        decided_by: str,
        reason: str,
        owner_approval_ref: str = "",
    ) -> IntakeProposal | None:
        if decision not in {"approved", "rejected"}:
            raise ValueError("decision must be approved or rejected")
        state = "approved" if decision == "approved" else "rejected"
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE intake_proposals
                SET state = ?, decided_by = ?, decision_reason = ?, owner_approval_ref = ?
                WHERE id = ? AND state = 'pending'
                """,
                (state, decided_by, reason, owner_approval_ref, proposal_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM intake_proposals WHERE id = ?", (proposal_id,)).fetchone()
        return self._row_to_prop(row) if row else None

    @staticmethod
    def _row_to_prop(row: sqlite3.Row) -> IntakeProposal:
        return IntakeProposal(
            id=row["id"],
            state=row["state"],
            subject=row["subject"],
            body_plain=row["body_plain"],
            sender=row["sender"],
            message_id=row["message_id"],
            dispatch=json.loads(row["dispatch_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            decided_by=row["decided_by"],
            decision_reason=row["decision_reason"],
            owner_approval_ref=row["owner_approval_ref"],
        )


def default_intake_proposal_store() -> IntakeProposalStore:
    return IntakeProposalStore(_config_dir() / "intake_proposals.sqlite3")
