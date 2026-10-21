"""Normalized inbound mail models (provider-neutral)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class IntakeDisposition(str, Enum):
    """Post-verification routing for one inbound message."""

    ACCEPT = "accept"
    QUARANTINE = "quarantine"
    REJECT = "reject"


@dataclass(frozen=True)
class VerificationOutcome:
    """Result of sender and replay checks."""

    disposition: IntakeDisposition
    reasons: tuple[str, ...] = ()
    dmarc_pass: bool | None = None
    sender_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "reasons": list(self.reasons),
            "dmarc_pass": self.dmarc_pass,
            "sender_allowed": self.sender_allowed,
        }


@dataclass(frozen=True)
class InboundMessage:
    """Provider-neutral representation of one received email.

    Pre: ``message_id`` is non-empty when the adapter could read it.
    Post: ``raw_headers`` and ``body_plain`` are treated as untrusted.
    """

    message_id: str
    from_address: str
    from_domain: str
    to_addresses: tuple[str, ...]
    subject: str
    body_plain: str
    received_at: datetime
    authentication_results: str = ""
    raw_headers: dict[str, str] = field(default_factory=dict)
    size_bytes: int = 0
    attachment_names: tuple[str, ...] = ()
    attachment_refs: tuple[str, ...] = ()
    provider: str = "unknown"
    provider_uid: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "from_address": self.from_address,
            "from_domain": self.from_domain,
            "to_addresses": list(self.to_addresses),
            "subject": self.subject,
            "body_plain": self.body_plain,
            "received_at": self.received_at.isoformat(),
            "authentication_results": self.authentication_results,
            "size_bytes": self.size_bytes,
            "attachment_names": list(self.attachment_names),
            "attachment_refs": list(self.attachment_refs),
            "provider": self.provider,
            "provider_uid": self.provider_uid,
        }
