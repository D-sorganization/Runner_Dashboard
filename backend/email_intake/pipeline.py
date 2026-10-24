"""End-to-end intake: verify, record, create work proposal."""

from __future__ import annotations

import logging
from typing import Any

from email_intake.intake_store import IntakeStore, default_intake_store
from email_intake.models import InboundMessage, IntakeDisposition
from email_intake.dispatch_role import UnknownStaffRoleError, normalize_staff_role
from email_intake.parse_request import parse_dispatch_hints
from email_intake.policy import MailPolicy
from email_intake.verify import verify_inbound
from intake_proposals.store import IntakeProposalStore, default_intake_proposal_store
from staff.audit import record_audit
from staff.rm_mail_policy import get_cached_mail_policy, policy_from_rm_root

log = logging.getLogger("dashboard.email_intake.pipeline")


def process_inbound_message(
    message: InboundMessage,
    *,
    policy: MailPolicy | None = None,
    intake_store: IntakeStore | None = None,
    proposal_store: IntakeProposalStore | None = None,
) -> dict[str, Any]:
    """Verify one message and optionally create a pending intake proposal."""
    pol = policy or get_cached_mail_policy() or policy_from_rm_root()
    store = intake_store or default_intake_store()
    proposals = proposal_store or default_intake_proposal_store()
    seen: set[str] = set()
    if message.message_id and store.has_message_id(message.message_id):
        seen.add(message.message_id)
    counts = {message.from_address.lower(): store.count_sender_last_hour(message.from_address)}
    outcome = verify_inbound(message, pol, seen_message_ids=seen, sender_counts_last_hour=counts)
    received_ts = message.received_at.timestamp()
    store.record(
        message_id=message.message_id or f"anon-{received_ts}",
        from_address=message.from_address,
        disposition=outcome.disposition.value,
        received_at=received_ts,
        detail={"reasons": list(outcome.reasons), "verification": outcome.to_dict(), "message": message.to_dict()},
    )
    record_audit(
        action="email_intake",
        target=message.message_id or "unknown",
        principal="email-intake",
        surface="scheduler",
        outcome=outcome.disposition.value,
        detail={"reasons": list(outcome.reasons), "from": message.from_address},
    )
    if outcome.disposition != IntakeDisposition.ACCEPT:
        return {"status": outcome.disposition.value, "reasons": list(outcome.reasons)}
    hints = parse_dispatch_hints(message.body_plain)
    if hints is None:
        store.record(
            message_id=message.message_id or f"anon-role-{received_ts}",
            from_address=message.from_address,
            disposition="quarantine",
            received_at=received_ts,
            detail={"reasons": ["missing_role"], "message": message.to_dict()},
        )
        record_audit(
            action="email_intake",
            target=message.message_id or "unknown",
            principal="email-intake",
            surface="scheduler",
            outcome="quarantine",
            detail={"reasons": ["missing_role"]},
        )
        return {"status": "quarantine", "reasons": ["missing_role"]}
    try:
        role = normalize_staff_role(hints.role)
    except UnknownStaffRoleError as exc:
        store.record(
            message_id=message.message_id or f"anon-badrole-{received_ts}",
            from_address=message.from_address,
            disposition="quarantine",
            received_at=received_ts,
            detail={"reasons": ["invalid_role"], "error": str(exc), "message": message.to_dict()},
        )
        record_audit(
            action="email_intake",
            target=message.message_id or "unknown",
            principal="email-intake",
            surface="scheduler",
            outcome="quarantine",
            detail={"reasons": ["invalid_role"], "error": str(exc)},
        )
        return {"status": "quarantine", "reasons": ["invalid_role"], "error": str(exc)}
    dispatch = {
        "role": role,
        "repo": hints.repo,
        "issue": hints.issue,
        "prompt": hints.prompt,
    }
    proposal = proposals.create_pending(
        subject=message.subject,
        body_plain=message.body_plain,
        sender=message.from_address,
        message_id=message.message_id,
        dispatch=dispatch,
    )
    record_audit(
        action="intake_proposal_create",
        target=proposal.id,
        principal="email-intake",
        surface="scheduler",
        outcome="success",
        detail={"message_id": message.message_id, "sender": message.from_address},
    )
    return {"status": "proposal_created", "proposal_id": proposal.id}
