"""Untrusted mail content handling."""

from __future__ import annotations

import re
from email import policy as email_policy
from email.message import Message
from email.parser import BytesParser

from staff.redaction import redact_sensitive_content

_TAG_RE = re.compile(r"<[^>]+>")


def _payload_bytes(part: Message) -> bytes:
    raw = part.get_payload(decode=True)
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    return b""


def html_to_plain(text: str) -> str:
    """Strip tags without fetching remote resources."""
    return _TAG_RE.sub(" ", text).strip()


def extract_plain_body(raw_bytes: bytes) -> str:
    """Prefer text/plain; fall back to stripped text/html."""
    msg = BytesParser(policy=email_policy.default).parsebytes(raw_bytes)
    plain_parts: list[str] = []
    html_parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                payload = _payload_bytes(part)
                plain_parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
            elif ctype == "text/html":
                payload = _payload_bytes(part)
                html_parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
    else:
        payload = _payload_bytes(msg)
        body = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        if msg.get_content_type() == "text/html":
            html_parts.append(body)
        else:
            plain_parts.append(body)
    body = "\n".join(plain_parts).strip() if plain_parts else ""
    if not body and html_parts:
        body = html_to_plain("\n".join(html_parts))
    return redact_sensitive_content(body.strip())


def list_attachment_names(raw_bytes: bytes) -> tuple[str, ...]:
    msg = BytesParser(policy=email_policy.default).parsebytes(raw_bytes)
    names: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            filename = part.get_filename()
            if filename:
                names.append(filename)
    return tuple(names)
