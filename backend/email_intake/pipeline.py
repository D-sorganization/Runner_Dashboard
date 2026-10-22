"""End-to-end intake: verify and record (proposal creation lands in #2099)."""

from __future__ import annotations

import logging
from typing import Any

from email_intake.intake_store import IntakeStore, default_intake_store
from email_intake.models import InboundMessage, IntakeDisposition
from email_intake.parse_request import parse_dispatch_hints
from email_intake.policy import MailPolicy
from email_intake.verify import verify_inbound
from staff.audit import record_audit
from staff.rm_mail_policy import get_cached_mail_policy, policy_from_rm_root

log = logging.getLogger("dashboard.email_intake.pipeline")


def process_inbound_message(
    message: InboundMessage,
    *,
    policy: MailPolicy | None = None,
    intake_store: IntakeStore | None = None,
) -> dict[str, Any]:
    """Verify one message and persist intake audit metadata."""
    pol = policy or get_cached_mail_policy() or policy_from_rm_root()
    store = intake_store or default_intake_store()
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
    hints = parse_dispatch_hints(message.body_plain, default_role="")
    return {
        "status": "accepted",
        "dispatch": {
            "role": hints.role,
            "repo": hints.repo,
            "issue": hints.issue,
            "prompt": hints.prompt,
        },
    }
