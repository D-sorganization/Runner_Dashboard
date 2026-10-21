"""Inbound verification: allowlist, DMARC, replay, rate, size, age."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from email_intake.models import InboundMessage, IntakeDisposition, VerificationOutcome
from email_intake.policy import MailPolicy

_DMARC_PASS = re.compile(r"\bdmarc\s*=\s*pass\b", re.IGNORECASE)
_FROM_ALIGN = re.compile(r"header\.from\s*=\s*([a-z0-9._-]+)", re.IGNORECASE)


def dmarc_pass_aligned(authentication_results: str, from_domain: str) -> bool:
    """True when Authentication-Results reports dmarc=pass for ``from_domain``."""
    if not authentication_results or not from_domain:
        return False
    if not _DMARC_PASS.search(authentication_results):
        return False
    domain = from_domain.lower()
    for match in _FROM_ALIGN.finditer(authentication_results):
        if match.group(1).lower() == domain:
            return True
    # Some MTAs only include header.d alignment
    return f"header.d={domain}" in authentication_results.lower()


def verify_inbound(
    message: InboundMessage,
    policy: MailPolicy,
    *,
    now: datetime | None = None,
    seen_message_ids: set[str] | None = None,
    sender_counts_last_hour: dict[str, int] | None = None,
) -> VerificationOutcome:
    """Classify one message before content handling."""
    moment = now or datetime.now(UTC)
    reasons: list[str] = []
    if not policy.intake_enabled:
        return VerificationOutcome(IntakeDisposition.REJECT, ("intake_disabled",), None, False)
    if policy.max_message_bytes and message.size_bytes > policy.max_message_bytes:
        reasons.append("size_exceeded")
    max_age = timedelta(hours=policy.max_age_hours)
    if message.received_at.tzinfo is None:
        received = message.received_at.replace(tzinfo=UTC)
    else:
        received = message.received_at
    if moment - received > max_age:
        reasons.append("too_old")
    sender = message.from_address.lower()
    allowed = policy.sender_enabled(sender)
    if not allowed:
        reasons.append("sender_not_allowlisted")
    dmarc_ok: bool | None = None
    if policy.require_dmarc_pass:
        dmarc_ok = dmarc_pass_aligned(message.authentication_results, message.from_domain)
        if not dmarc_ok:
            reasons.append("dmarc_fail")
    if message.message_id and seen_message_ids is not None and message.message_id in seen_message_ids:
        reasons.append("duplicate_message_id")
    if sender_counts_last_hour is not None:
        count = sender_counts_last_hour.get(sender, 0)
        if count >= policy.rate_limit_per_sender_per_hour:
            reasons.append("rate_limited")
    if reasons:
        disposition = IntakeDisposition.QUARANTINE
        if "intake_disabled" in reasons:
            disposition = IntakeDisposition.REJECT
        return VerificationOutcome(disposition, tuple(reasons), dmarc_ok, allowed)
    return VerificationOutcome(IntakeDisposition.ACCEPT, (), dmarc_ok, allowed)
