"""InboundMessage and verification outcome models."""

from datetime import UTC, datetime

from email_intake.models import InboundMessage, IntakeDisposition, VerificationOutcome


def test_inbound_message_roundtrip_dict() -> None:
    msg = InboundMessage(
        message_id="<abc@example.com>",
        from_address="user@affinedrift.com",
        from_domain="affinedrift.com",
        to_addresses=("intake@work.affinedrift.com",),
        subject="Please run tests",
        body_plain="role: maintenance\nfix CI",
        received_at=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        authentication_results="dmarc=pass header.from=affinedrift.com",
        size_bytes=42,
        provider="imap",
    )
    data = msg.to_dict()
    assert data["message_id"] == "<abc@example.com>"
    assert data["from_domain"] == "affinedrift.com"


def test_verification_outcome_serializes() -> None:
    out = VerificationOutcome(
        disposition=IntakeDisposition.QUARANTINE,
        reasons=("dmarc_fail",),
        dmarc_pass=False,
        sender_allowed=True,
    )
    assert out.to_dict()["disposition"] == "quarantine"
