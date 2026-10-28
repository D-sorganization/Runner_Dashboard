from datetime import UTC, datetime, timedelta

from email_intake.models import InboundMessage
from email_intake.policy import AllowedSender, MailPolicy
from email_intake.verify import dmarc_pass_aligned, verify_inbound


def _msg(**kwargs: object) -> InboundMessage:
    defaults = {
        "message_id": "<1@test>",
        "from_address": "user@example.com",
        "from_domain": "example.com",
        "to_addresses": ("intake@test",),
        "subject": "hi",
        "body_plain": "role: barb\nplease triage",
        "received_at": datetime.now(UTC),
        "authentication_results": "dmarc=pass header.from=example.com",
        "size_bytes": 100,
    }
    defaults.update(kwargs)
    return InboundMessage(**defaults)  # type: ignore[arg-type]


def test_dmarc_aligned() -> None:
    assert dmarc_pass_aligned("dmarc=pass header.from=example.com", "example.com")


def test_quarantine_when_dmarc_fails() -> None:
    policy = MailPolicy(
        intake_enabled=True,
        require_dmarc_pass=True,
        max_age_hours=72,
        rate_limit_per_sender_per_hour=10,
        max_message_bytes=10000,
        allowed_senders=(AllowedSender("user@example.com", True),),
    )
    outcome = verify_inbound(_msg(authentication_results="dmarc=fail"), policy)
    assert outcome.disposition.value == "quarantine"
    assert "dmarc_fail" in outcome.reasons
