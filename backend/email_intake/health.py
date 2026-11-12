"""Intake health snapshot for fleet status."""

from __future__ import annotations

from typing import Any

from email_intake.config import load_email_intake_config
from email_intake.intake_store import default_intake_store
from staff.rm_mail_policy import get_cached_mail_policy


def email_intake_status() -> dict[str, Any]:
    cfg = load_email_intake_config()
    policy = get_cached_mail_policy()
    store = default_intake_store()
    return {
        "enabled": cfg.intake_enabled,
        "imap_configured": cfg.imap_configured(),
        "intake_address": cfg.intake_address,
        "policy_loaded": policy is not None,
        "policy_intake_enabled": policy.intake_enabled if policy else None,
        "quarantine_count": store.quarantine_count(),
    }
