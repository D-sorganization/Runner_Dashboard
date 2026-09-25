"""Action proposal operations and state machine transitions (SC-B2, Issue #1305)."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from typing import TYPE_CHECKING, Any

from staff.audit import record_audit
from staff.conversation_models import (
    _VALID_PROPOSAL_TRANSITIONS,
    PROPOSAL_RISKS,
    PROPOSAL_STATES,
    ActionProposalRecord,
    _now,
)

if TYPE_CHECKING:
    from staff.audit import StaffAuditStore

log = logging.getLogger("dashboard.staff.conversations.proposals")


def create_proposal(
    conn: sqlite3.Connection,
    lock: threading.RLock,
    message_id: str,
    thread_id: str = "",
    action: str = "",
    params: dict[str, Any] | None = None,
    risk: str = "low",
    proposal_id: str | None = None,
    principal: str = "",
    audit_store: StaffAuditStore | None = None,
) -> ActionProposalRecord:
    assert risk in PROPOSAL_RISKS, f"Invalid risk: {risk}"  # noqa: S101
    import uuid

    pid = proposal_id or f"prop_{uuid.uuid4().hex[:12]}"
    now = _now()
    param_dict = dict(params or {})

    rec = ActionProposalRecord(
        id=pid,
        message_id=message_id,
        thread_id=thread_id,
        action=action,
        params=param_dict,
        risk=risk,
        state="proposed",
        created_at=now,
    )

    with lock:
        conn.execute(
            "INSERT INTO action_proposals (id, message_id, thread_id, action, params, risk, state, "
            "decided_by, decided_at, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rec.id,
                rec.message_id,
                rec.thread_id,
                rec.action,
                json.dumps(rec.params),
                rec.risk,
                rec.state,
                rec.decided_by,
                rec.decided_at,
                rec.reason,
                rec.created_at,
            ),
        )

    record_audit(
        action="proposal_create",
        target=rec.id,
        principal=principal,
        surface="thread",
        thread_id=rec.thread_id,
        outcome="success",
        detail={"action": rec.action, "risk": rec.risk, "params": rec.params},
        fail_closed=True,
        store=audit_store,
    )
    return rec


def get_proposal(conn: sqlite3.Connection, lock: threading.RLock, proposal_id: str) -> ActionProposalRecord | None:
    with lock:
        row = conn.execute("SELECT * FROM action_proposals WHERE id = ?", (proposal_id,)).fetchone()
    return ActionProposalRecord.from_row(row) if row else None


def list_proposals(
    conn: sqlite3.Connection,
    lock: threading.RLock,
    thread_id: str | None = None,
    message_id: str | None = None,
    state: str | None = None,
    limit: int = 100,
) -> list[ActionProposalRecord]:
    clauses: list[str] = []
    params: list[Any] = []
    if thread_id:
        clauses.append("thread_id = ?")
        params.append(thread_id)
    if message_id:
        clauses.append("message_id = ?")
        params.append(message_id)
    if state:
        clauses.append("state = ?")
        params.append(state)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"SELECT * FROM action_proposals {where} ORDER BY created_at DESC LIMIT ?"  # noqa: S608
    params.append(limit)
    with lock:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [ActionProposalRecord.from_row(r) for r in rows]


def decide_proposal(
    conn: sqlite3.Connection,
    lock: threading.RLock,
    proposal_id: str,
    state: str,
    decided_by: str,
    reason: str = "",
    audit_store: StaffAuditStore | None = None,
) -> ActionProposalRecord:
    assert state in (
        "approved",
        "denied",
    ), f"Decision must be 'approved' or 'denied', got {state}"  # noqa: S101
    with lock:
        prop = get_proposal(conn, lock, proposal_id)
        if not prop:
            raise ValueError(f"Proposal {proposal_id} not found")
        # A failed proposal may be decided again: approving it is the explicit retry (#1485).
        if prop.state not in ("proposed", "failed"):
            raise ValueError(f"Cannot decide proposal in state '{prop.state}' (must be 'proposed' or 'failed')")
        now = _now()
        conn.execute(
            "UPDATE action_proposals SET state = ?, decided_by = ?, decided_at = ?, reason = ? WHERE id = ?",
            (state, decided_by, now, reason, proposal_id),
        )
        updated = get_proposal(conn, lock, proposal_id)
        assert updated is not None  # noqa: S101

    audit_action = "proposal_approve" if state == "approved" else "proposal_deny"
    record_audit(
        action=audit_action,
        target=proposal_id,
        principal=decided_by,
        surface="thread",
        thread_id=prop.thread_id,
        outcome="success",
        detail={"reason": reason, "previous_state": prop.state},
        fail_closed=True,
        store=audit_store,
    )
    return updated


def transition_proposal_state(
    conn: sqlite3.Connection,
    lock: threading.RLock,
    proposal_id: str,
    new_state: str,
    decided_by: str = "",
    reason: str = "",
    audit_store: StaffAuditStore | None = None,
) -> ActionProposalRecord:
    assert new_state in PROPOSAL_STATES, f"Invalid proposal state: {new_state}"  # noqa: S101
    with lock:
        prop = get_proposal(conn, lock, proposal_id)
        if not prop:
            raise ValueError(f"Proposal {proposal_id} not found")
        allowed = _VALID_PROPOSAL_TRANSITIONS.get(prop.state, set())
        if new_state not in allowed:
            raise ValueError(
                f"Invalid proposal transition from '{prop.state}' to '{new_state}'. Allowed: {sorted(allowed)}"
            )
        now = _now()
        dec_by = decided_by or prop.decided_by
        dec_at = now if new_state in {"approved", "denied"} else prop.decided_at
        r = reason or prop.reason
        conn.execute(
            "UPDATE action_proposals SET state = ?, decided_by = ?, decided_at = ?, reason = ? WHERE id = ?",
            (new_state, dec_by, dec_at, r, proposal_id),
        )
        updated = get_proposal(conn, lock, proposal_id)
        assert updated is not None  # noqa: S101

    audit_action = f"proposal_{new_state}" if new_state in {"approved", "denied", "executing"} else "proposal_create"
    if new_state == "executing":
        audit_action = "proposal_execute"
    record_audit(
        action=audit_action,
        target=proposal_id,
        principal=dec_by,
        surface="thread",
        thread_id=prop.thread_id,
        outcome="success",
        detail={"new_state": new_state, "previous_state": prop.state, "reason": r},
        fail_closed=True,
        store=audit_store,
    )
    return updated
