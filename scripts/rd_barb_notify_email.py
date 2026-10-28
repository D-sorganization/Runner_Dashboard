"""Email adapter for the rd-barb direct notify path (RD #3075): stdlib SMTP, TLS required, config only.

Settings come from the config mapping (``RD_BARB_*`` keys); the password is read from the environment
variable *named* by ``RD_BARB_EMAIL_PASSWORD_ENV`` (default ``RD_BARB_EMAIL_PASSWORD``) and never stored.

  RD_BARB_EMAIL_HOST, RD_BARB_EMAIL_FROM, RD_BARB_EMAIL_TO, RD_BARB_EMAIL_USER   required
  RD_BARB_EMAIL_PORT                                                           optional, default 587
  RD_BARB_EMAIL_PASSWORD_ENV                                                   optional env var name

Port 465 uses implicit TLS; any other port requires STARTTLS. Clear text is never used. Errors are
raised as short codes chosen by exception type; server text, addresses and the password never leave here.
"""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Mapping
from email.message import EmailMessage
from typing import Any

SMTP_TIMEOUT_S = 15.0
IMPLICIT_TLS_PORT = 465
DEFAULT_PORT = 587
DEFAULT_PASSWORD_ENV = "RD_BARB_EMAIL_PASSWORD"  # noqa: S105 - the variable's name, not a secret
REQUIRED_KEYS = frozenset({"RD_BARB_EMAIL_HOST", "RD_BARB_EMAIL_FROM", "RD_BARB_EMAIL_TO", "RD_BARB_EMAIL_USER"})
MAX_SUBJECT_CHARS = 100


def build_message(msg: Mapping[str, Any], *, sender: str, recipient: str) -> EmailMessage:
    """Subject from the kind and summary; body is the envelope text, its options and its id."""
    subject = f"[rd-barb {msg['kind']}] {msg['summary']}"[:MAX_SUBJECT_CHARS]
    lines = [str(msg.get("body") or msg["summary"])]
    if msg.get("recommended_action"):
        lines.append(f"Recommended action: {msg['recommended_action']}")
    for option in msg.get("options") or []:
        lines.append(f"- {option['label']}{' (recommended)' if option.get('recommended') else ''}")
    if msg.get("recommendation_reason"):
        lines.append(f"Why: {msg['recommendation_reason']}")
    lines.append(f"Id: {msg['dedupe_key']}")
    out = EmailMessage()
    out["From"], out["To"], out["Subject"] = sender, recipient, subject
    out.set_content("\n".join(lines))
    return out


class EmailChannel:
    """One SMTP delivery per ``send``; ``error_cls(code)`` is the caller's channel error."""

    name = "email"

    def __init__(
        self,
        cfg: Mapping[str, str],
        *,
        error_cls: type[Exception],
        environ: Mapping[str, str],
        smtp: Any = None,
        smtp_ssl: Any = None,
    ) -> None:
        self._cfg, self._error, self._environ = dict(cfg), error_cls, environ
        self._smtp, self._smtp_ssl = smtp, smtp_ssl

    def send(self, msg: dict[str, Any]) -> None:
        """Deliver ``msg`` or raise ``error_cls`` with a code; never includes server text or addresses."""
        host, port, password = self._settings()
        message = build_message(msg, sender=self._cfg["RD_BARB_EMAIL_FROM"], recipient=self._cfg["RD_BARB_EMAIL_TO"])
        try:
            self._deliver(host, port, password, message)
        except smtplib.SMTPAuthenticationError:
            raise self._error("email_auth_failed") from None
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
            raise self._error("email_rejected") from None
        except smtplib.SMTPNotSupportedError:
            raise self._error("email_tls_unsupported") from None
        except smtplib.SMTPException:
            raise self._error("email_smtp_error") from None
        except TimeoutError:
            raise self._error("email_timeout") from None
        except ssl.SSLError:
            raise self._error("email_tls_failed") from None
        except OSError:
            raise self._error("email_connect_failed") from None

    def _settings(self) -> tuple[str, int, str]:
        if not all(self._cfg.get(key, "").strip() for key in REQUIRED_KEYS):
            raise self._error("email_not_configured")
        try:
            port = int(self._cfg.get("RD_BARB_EMAIL_PORT") or DEFAULT_PORT)
        except ValueError:
            raise self._error("email_not_configured") from None
        password = self._environ.get(self._cfg.get("RD_BARB_EMAIL_PASSWORD_ENV") or DEFAULT_PASSWORD_ENV, "")
        if not password:
            raise self._error("email_not_configured")
        return self._cfg["RD_BARB_EMAIL_HOST"], port, password

    def _deliver(self, host: str, port: int, password: str, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        if port == IMPLICIT_TLS_PORT:
            factory = self._smtp_ssl or smtplib.SMTP_SSL
            conn_cm = factory(host, port, timeout=SMTP_TIMEOUT_S, context=context)
        else:
            conn_cm = (self._smtp or smtplib.SMTP)(host, port, timeout=SMTP_TIMEOUT_S)
        with conn_cm as conn:
            if port != IMPLICIT_TLS_PORT:
                conn.starttls(context=context)
            conn.login(self._cfg["RD_BARB_EMAIL_USER"], password)
            conn.send_message(message)


def email_channel(
    cfg: Mapping[str, str],
    *,
    error_cls: type[Exception],
    environ: Mapping[str, str] | None = None,
    smtp: Any = None,
    smtp_ssl: Any = None,
) -> EmailChannel:
    """Build the channel; ``environ`` defaults to ``os.environ`` (read lazily for the password only)."""
    if environ is None:
        import os  # noqa: PLC0415

        environ = os.environ
    return EmailChannel(cfg, error_cls=error_cls, environ=environ, smtp=smtp, smtp_ssl=smtp_ssl)
