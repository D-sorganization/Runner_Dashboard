from datetime import UTC, datetime
from pathlib import Path

import pytest

from email_intake.intake_store import IntakeStore
from email_intake.models import InboundMessage
from email_intake.pipeline import process_inbound_message
from email_intake.policy import AllowedSender, MailPolicy
from intake_proposals.store import IntakeProposalStore
from staff.store import _config_dir


@pytest.fixture
def stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("staff.store._config_dir", lambda: tmp_path)
    monkeypatch.setattr("email_intake.intake_store._config_dir", lambda: tmp_path)
    monkeypatch.setattr("intake_proposals.store._config_dir", lambda: tmp_path)
    yield IntakeStore(tmp_path / "intake.sqlite3"), IntakeProposalStore(tmp_path / "props.sqlite3")


def test_accept_creates_proposal(stores) -> None:
    intake_store, proposal_store = stores
    policy = MailPolicy(
        intake_enabled=True,
        require_dmarc_pass=True,
        max_age_hours=72,
        rate_limit_per_sender_per_hour=10,
        max_message_bytes=10000,
        allowed_senders=(AllowedSender("dieterolson@gmail.com", True),),
    )
    msg = InboundMessage(
        message_id="<abc@test>",
        from_address="dieterolson@gmail.com",
        from_domain="gmail.com",
        to_addresses=("intake@test",),
        subject="work",
        body_plain="role: barb\nfix queue",
        received_at=datetime.now(UTC),
        authentication_results="dmarc=pass header.from=gmail.com",
        size_bytes=50,
    )
    out = process_inbound_message(
        msg,
        policy=policy,
        intake_store=intake_store,
        proposal_store=proposal_store,
    )
    assert out["status"] == "proposal_created"
    pending = proposal_store.list(state="pending")
    assert len(pending) == 1
    assert pending[0].dispatch["role"] == "barb"
