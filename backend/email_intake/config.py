"""Email intake configuration from environment (secrets never logged)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _read_secret_file(path: str) -> str:
    p = Path(path).expanduser()
    return p.read_text(encoding="utf-8").strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class EmailIntakeConfig:
    """Runtime knobs for inbound/outbound mail adapters."""

    intake_enabled: bool
    intake_address: str
    intake_domain: str
    imap_host: str
    imap_user: str
    imap_password: str
    imap_port: int
    imap_use_ssl: bool
    imap_folder: str
    poll_interval_seconds: int
    imap_idle_enabled: bool
    smtp_outbound_enabled: bool

    def imap_configured(self) -> bool:
        return bool(self.imap_host and self.imap_user and self.imap_password)


def load_email_intake_config() -> EmailIntakeConfig:
    """Load config from ``MAIL_INTAKE_*`` environment variables."""
    password = os.environ.get("MAIL_INTAKE_IMAP_PASSWORD", "")
    password_file = os.environ.get("MAIL_INTAKE_IMAP_PASSWORD_FILE", "")
    if not password and password_file:
        password = _read_secret_file(password_file)
    port_raw = os.environ.get("MAIL_INTAKE_IMAP_PORT", "993").strip()
    poll_raw = os.environ.get("MAIL_INTAKE_POLL_INTERVAL_SECONDS", "120").strip()
    return EmailIntakeConfig(
        intake_enabled=_env_bool("MAIL_INTAKE_ENABLED", False),
        intake_address=os.environ.get("MAIL_INTAKE_ADDRESS", "").strip().lower(),
        intake_domain=os.environ.get("MAIL_INTAKE_DOMAIN", "").strip().lower(),
        imap_host=os.environ.get("MAIL_INTAKE_IMAP_HOST", "").strip(),
        imap_user=os.environ.get("MAIL_INTAKE_IMAP_USER", "").strip(),
        imap_password=password,
        imap_port=int(port_raw) if port_raw.isdigit() else 993,
        imap_use_ssl=_env_bool("MAIL_INTAKE_IMAP_SSL", True),
        imap_folder=os.environ.get("MAIL_INTAKE_IMAP_FOLDER", "INBOX").strip() or "INBOX",
        poll_interval_seconds=max(30, int(poll_raw)) if poll_raw.isdigit() else 120,
        imap_idle_enabled=_env_bool("MAIL_INTAKE_IMAP_IDLE", False),
        default_dispatch_role=os.environ.get("MAIL_INTAKE_DEFAULT_DISPATCH_ROLE", "barb").strip() or "barb",
        smtp_outbound_enabled=_env_bool("MAIL_INTAKE_SMTP_OUTBOUND_ENABLED", False),
    )
