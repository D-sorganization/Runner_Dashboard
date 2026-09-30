"""Action proposal operations and state machine transitions (SC-B2, Issue #1305)."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from staff.audit import record_audit
from staff.conversation_models import (
    _VALID_PROPOSAL_TRANSITIONS,
    DEFAULTS_IF_SILENT,
    PROPOSAL_RISKS,
    PROPOSAL_STATES,
    PROPOSAL_TTL_SECONDS,
    SILENT_APPROVE_RISKS,
    ActionProposalRecord,
    _now,
)
from staff.redaction import redact_sensitive_content, redact_value

if TYPE_CHECKING:
    from staff.audit import StaffAuditStore

log = logging.getLogger("dashboard.staff.conversations.proposals")


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def validate_decision_sla(decide_by: str | None, default_if_silent: str, risk: str, created_at: str) -> None:
    """The decision SLA a source may attach to a proposal (WP-2.6, #1607).

    Pre: ``default_if_silent`` is one of :data:`DEFAULTS_IF_SILENT` and needs a ``decide_by``;
    ``decide_by`` is an ISO-8601 datetime no later than the proposal's expiry; ``approve`` by
    silence only for :data:`SILENT_APPROVE_RISKS`. Raises ``ValueError`` otherwise.
    """
    if default_if_silent not in DEFAULTS_IF_SILENT:
        raise ValueError(f"default_if_silent must be one of {DEFAULTS_IF_SILENT}, got {default_if_silent!r}")
    if decide_by is None:
        if default_if_silent:
            raise ValueError("default_if_silent needs a decide_by deadline")
        return
    try:
        deadline = _parse_iso(decide_by)
    except ValueError as exc:
        raise ValueError(f"decide_by must be an ISO-8601 datetime, got {decide_by!r}") from exc
    if deadline > _parse_iso(created_at) + timedelta(seconds=PROPOSAL_TTL_SECONDS):
        raise ValueError(f"decide_by {decide_by} is after the proposal would expire ({PROPOSAL_TTL_SECONDS}s)")
    if default_if_silent == "approve" and risk not in SILENT_APPROVE_RISKS:
        raise ValueError(f"approve by silence is only allowed for a low-risk action, not risk {risk!r}")


def find_pending_proposal(
    conn: sqlite3.Connection,
    lock: threading.RLock,
    thread_id: str,
    action: str,
    params: dict[str, Any] | None = None,
    decide_by: str | None = None,
    default_if_silent: str = "",
) -> ActionProposalRecord | None:
    """Find an existing pending proposal in ``thread_id`` with identical ``action`` and ``params`` (#1716).

    Returns the pending proposal if found, or None.
    """
    param_dict = redact_value(dict(params or {}))
    with lock:
        rows = conn.execute(
            "SELECT * FROM action_proposals WHERE thread_id = ? AND action = ? AND state = 'proposed'",
            (thread_id, action),
        ).fetchall()
        for r in rows:
            rec = ActionProposalRecord.from_row(r)
            if (
                rec.params == param_dict
                and rec.decide_by == decide_by
                and (rec.default_if_silent or "") == (default_if_silent or "")
            ):
                return rec
    return None


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
    decide_by: str | None = None,
    default_if_silent: str = "",
    deduplicate: bool = False,
) -> ActionProposalRecord:
    assert risk in PROPOSAL_RISKS, f"Invalid risk: {risk}"  # noqa: S101
    import uuid

    now = _now()
    validate_decision_sla(decide_by, default_if_silent, risk, now)

    param_dict = redact_value(dict(params or {}))

    # De-duplicate identical pending proposals within the same thread when requested (#1716)
    if deduplicate and proposal_id is None and thread_id and action:
        existing = find_pending_proposal(
            conn,
            lock,
            thread_id=thread_id,
            action=action,
            params=params,
            decide_by=decide_by,
            default_if_silent=default_if_silent,
        )
        if existing is not None:
            log.info(
                "Reusing existing pending proposal %s for action %s in thread %s (#1716)",
                existing.id,
                action,
                thread_id,
            )
            return existing

    pid = proposal_id or f"prop_{uuid.uuid4().hex[:12]}"

    rec = ActionProposalRecord(
        id=pid,
        message_id=message_id,
        thread_id=thread_id,
        action=action,
        params=param_dict,
        risk=risk,
        state="proposed",
        created_at=now,
        decide_by=decide_by,
        default_if_silent=default_if_silent,
    )

    with lock:
        conn.execute(
            "INSERT INTO action_proposals (id, message_id, thread_id, action, params, risk, state, "
            "decided_by, decided_at, reason, created_at, decide_by, default_if_silent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                rec.decide_by,
                rec.default_if_silent,
            ),
        )

    record_audit(
        action="proposal_create",
        target=rec.id,
        principal=principal,
        surface="thread",
        thread_id=rec.thread_id,
        outcome="success",
        detail={
            "action": rec.action,
            "risk": rec.risk,
            "params": rec.params,
            "decide_by": rec.decide_by,
            "default_if_silent": rec.default_if_silent,
        },
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
    after_deadline: tuple[str, str] | None = None,
) -> list[ActionProposalRecord]:
    """Newest first; with ``after_deadline`` (``("", "")`` starts), only proposals with a ``decide_by``.

    The deadline mode pages in ``(decide_by, id)`` order from that cursor, so a caller can walk every
    pending deadline instead of the newest ``limit`` (BR-04, #1798).
    """
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
    order = "created_at DESC"
    if after_deadline is not None:
        clauses.append("decide_by IS NOT NULL AND (decide_by, id) > (?, ?)")
        params.extend(after_deadline)
        order = "decide_by, id"
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"SELECT * FROM action_proposals {where} ORDER BY {order} LIMIT ?"  # noqa: S608
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
            (state, decided_by, now, redact_sensitive_content(reason), proposal_id),
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
        r = redact_sensitive_content(reason) or prop.reason
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
