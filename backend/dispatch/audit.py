"""Dispatch audit log — append-only audit records for every dispatch decision.

Public API:
- DispatchAuditLogEntry (dataclass)
- build_audit_log_entry(envelope, validation, *, detail) -> DispatchAuditLogEntry
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from dispatch.registry import DispatchAccess
from dispatch.validate import DispatchValidationResult
from time_utils import utc_now_iso

log = logging.getLogger("dashboard.dispatch.audit")


@dataclass(frozen=True, slots=True)
class DispatchAuditLogEntry:
    """Append-only audit record for a dispatch decision."""

    event_id: str
    envelope_id: str
    action: str
    access: DispatchAccess
    source: str
    target: str
    requested_by: str
    decision: str
    detail: str
    confirmation_state: str
    payload_snapshot: dict[str, Any]
    recorded_at: str

    principal: str = ""
    on_behalf_of: str = ""
    correlation_id: str = ""
    args_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "envelope_id": self.envelope_id,
            "action": self.action,
            "access": self.access.value,
            "source": self.source,
            "target": self.target,
            "requested_by": self.requested_by,
            "principal": self.principal,
            "on_behalf_of": self.on_behalf_of,
            "correlation_id": self.correlation_id,
            "args_hash": self.args_hash,
            "decision": self.decision,
            "detail": self.detail,
            "confirmation_state": self.confirmation_state,
            "payload_snapshot": dict(self.payload_snapshot),
            "recorded_at": self.recorded_at,
        }


def _confirmation_state(validation: DispatchValidationResult, confirmation: object) -> str:
    if confirmation is None:
        return "missing" if validation.confirmation_required else "not-required"
    if validation.confirmation_required and validation.reason.startswith("confirmation must "):
        return "invalid"
    return "approved"


def build_audit_log_entry(
    envelope: object,
    validation: DispatchValidationResult,
    *,
    detail: str = "",
) -> DispatchAuditLogEntry:
    action = validation.action
    access = DispatchAccess.READ_ONLY if action is None else action.access

    return DispatchAuditLogEntry(
        event_id=uuid4().hex,
        envelope_id=envelope.envelope_id,  # type: ignore[attr-defined]
        action=envelope.action,  # type: ignore[attr-defined]
        access=access,
        source=envelope.source,  # type: ignore[attr-defined]
        target=envelope.target,  # type: ignore[attr-defined]
        requested_by=envelope.requested_by,  # type: ignore[attr-defined]
        principal=envelope.principal,  # type: ignore[attr-defined]
        on_behalf_of=envelope.on_behalf_of,  # type: ignore[attr-defined]
        correlation_id=envelope.correlation_id,  # type: ignore[attr-defined]
        args_hash=hashlib.sha256(json.dumps(envelope.payload, sort_keys=True).encode()).hexdigest(),  # type: ignore[attr-defined]
        decision="accepted" if validation.accepted else "rejected",
        detail=detail or validation.reason,
        confirmation_state=_confirmation_state(validation, envelope.confirmation),  # type: ignore[attr-defined]
        payload_snapshot=dict(envelope.payload),  # type: ignore[attr-defined]
        recorded_at=utc_now_iso(),
    )


@dataclass(frozen=True, slots=True)
class CodeRequestTransitionAuditEntry:
    """Audit entry for a Code Request state transition (CR-2, issue #1282)."""

    actor: str
    from_state: str
    to_state: str
    reason: str
    timestamp: str
    override: bool = False
    request_id: str = ""
    repository: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": "code_request_transition",
            "actor": self.actor,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "override": self.override,
            "request_id": self.request_id,
            "repository": self.repository,
        }


def record_code_request_transition(
    entry: CodeRequestTransitionAuditEntry | dict[str, Any],
) -> None:
    """Record a code request transition event into the dispatch audit log."""
    payload = entry.to_dict() if isinstance(entry, CodeRequestTransitionAuditEntry) else dict(entry)
    log.info(
        "audit: code_request_transition request_id=%s repo=%s %s -> %s actor=%s override=%s",
        payload.get("request_id"),
        payload.get("repository"),
        payload.get("from_state"),
        payload.get("to_state"),
        payload.get("actor"),
        payload.get("override"),
    )
    audit_file = Path.home() / "actions-runners" / "dashboard" / "dispatch_audit.ndjson"
    try:
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        with audit_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except OSError as err:
        log.warning("failed to append code request audit entry: %s", err)
