"""IMAP pull adapter (stdlib imaplib)."""

from __future__ import annotations

import email.utils
import imaplib
import logging
from datetime import UTC, datetime
from email import policy as email_policy
from email.parser import BytesParser

from email_intake.adapters.base import InboundAdapter, InboundFetchResult
from email_intake.config import EmailIntakeConfig
from email_intake.content import extract_plain_body, list_attachment_names
from email_intake.models import InboundMessage

log = logging.getLogger("dashboard.email_intake.imap")


def _parse_address(value: str) -> tuple[str, str]:
    name, addr = email.utils.parseaddr(value)
    addr = addr.lower()
    domain = addr.split("@", 1)[1] if "@" in addr else ""
    return addr, domain


class ImapInboundAdapter(InboundAdapter):
    def __init__(self, config: EmailIntakeConfig) -> None:
        self._config = config
        self._last_uid: int | None = None

    @property
    def name(self) -> str:
        return "imap"

    def fetch_new(self) -> InboundFetchResult:
        if not self._config.imap_configured():
            return InboundFetchResult(errors=("imap_not_configured",))
        messages: list[InboundMessage] = []
        errors: list[str] = []
        try:
            if self._config.imap_use_ssl:
                client: imaplib.IMAP4 = imaplib.IMAP4_SSL(self._config.imap_host, self._config.imap_port)
            else:
                client = imaplib.IMAP4(self._config.imap_host, self._config.imap_port)
            client.login(self._config.imap_user, self._config.imap_password)
            client.select(self._config.imap_folder)
            criteria = "UNSEEN" if self._last_uid is None else f"UID {self._last_uid + 1}:*"
            typ, data = client.uid("search", "", criteria)
            if typ != "OK" or not data or not data[0]:
                client.logout()
                return InboundFetchResult(messages=tuple(messages))
            uids = [int(x) for x in data[0].split()]
            for uid in uids:
                typ, fetched = client.uid("fetch", str(uid), "(RFC822)")
                if typ != "OK" or not fetched or fetched[0] is None:
                    continue
                raw = fetched[0][1]
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                msg = BytesParser(policy=email_policy.default).parsebytes(raw)
                message_id = (msg.get("Message-ID") or f"uid-{uid}").strip()
                from_addr, from_domain = _parse_address(msg.get("From", ""))
                tos = tuple(email.utils.getaddresses(msg.get_all("To", [])))
                to_addresses = tuple(addr.lower() for _, addr in tos if addr)
                auth_results = msg.get("Authentication-Results", "") or ""
                received = email.utils.parsedate_to_datetime(msg.get("Date", "")) or datetime.now(UTC)
                if received.tzinfo is None:
                    received = received.replace(tzinfo=UTC)
                body = extract_plain_body(raw)
                names = list_attachment_names(raw)
                messages.append(
                    InboundMessage(
                        message_id=message_id,
                        from_address=from_addr,
                        from_domain=from_domain,
                        to_addresses=to_addresses,
                        subject=(msg.get("Subject") or "").strip(),
                        body_plain=body,
                        received_at=received,
                        authentication_results=auth_results,
                        size_bytes=len(raw),
                        attachment_names=names,
                        provider="imap",
                        provider_uid=str(uid),
                    )
                )
                self._last_uid = max(self._last_uid or 0, uid)
            client.logout()
        except Exception as exc:  # noqa: BLE001 — adapter surfaces error string only
            log.warning("IMAP fetch failed: %s", exc.__class__.__name__)
            errors.append("imap_fetch_failed")
        return InboundFetchResult(messages=tuple(messages), errors=tuple(errors))
