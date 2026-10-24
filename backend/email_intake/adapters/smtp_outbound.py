"""Outbound SMTP adapter (disabled unless explicitly enabled)."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from email_intake.adapters.base import SmtpAdapter
from email_intake.config import EmailIntakeConfig

log = logging.getLogger("dashboard.email_intake.smtp")


class DisabledSmtpAdapter(SmtpAdapter):
    """Default outbound adapter: refuses all sends."""

    @property
    def name(self) -> str:
        return "disabled"

    def send_plain(self, *, to_address: str, subject: str, body: str) -> None:
        raise RuntimeError("outbound SMTP is disabled")


class SmtpOutboundAdapter(SmtpAdapter):
    """Optional SMTP receipts; allowlist enforced by caller."""

    def __init__(self, config: EmailIntakeConfig, allowlist: frozenset[str]) -> None:
        self._config = config
        self._allowlist = allowlist

    @property
    def name(self) -> str:
        return "smtp"

    def send_plain(self, *, to_address: str, subject: str, body: str) -> None:
        if not self._config.smtp_outbound_enabled:
            raise RuntimeError("MAIL_INTAKE_SMTP_OUTBOUND_ENABLED is not set")
        addr = to_address.strip().lower()
        if addr not in self._allowlist:
            raise PermissionError("recipient not allowlisted")
        log.info("smtp outbound queued to %s (subject len=%d)", addr, len(subject))
        # Host/user/password wiring ships when outbound slice is enabled on hub.
        raise NotImplementedError("smtp transport not configured on this node")
