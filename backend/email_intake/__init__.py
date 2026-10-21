"""Provider-neutral inbound email intake (epic #2092)."""

from email_intake.config import EmailIntakeConfig, load_email_intake_config
from email_intake.models import InboundMessage, IntakeDisposition, VerificationOutcome

__all__ = [
    "EmailIntakeConfig",
    "InboundMessage",
    "IntakeDisposition",
    "VerificationOutcome",
    "load_email_intake_config",
]
