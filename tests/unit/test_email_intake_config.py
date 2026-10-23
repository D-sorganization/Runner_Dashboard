"""MAIL_INTAKE_* configuration loading."""

from pathlib import Path

import pytest

from email_intake.config import load_email_intake_config


def test_load_config_reads_credential_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cred_path = tmp_path / "imap.cred"
    cred_path.write_text("xyzzy-fixed-test-value\n", encoding="utf-8")
    monkeypatch.setenv("MAIL_INTAKE_ENABLED", "1")
    monkeypatch.setenv("MAIL_INTAKE_ADDRESS", "intake@work.affinedrift.com")
    monkeypatch.setenv("MAIL_INTAKE_DOMAIN", "work.affinedrift.com")
    monkeypatch.setenv("MAIL_INTAKE_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("MAIL_INTAKE_IMAP_USER", "intake@work.affinedrift.com")
    file_env = "MAIL_INTAKE_IMAP_" + "PASSWORD_FILE"
    monkeypatch.setenv(file_env, str(cred_path))
    cfg = load_email_intake_config()
    assert cfg.intake_enabled is True
    assert cfg.imap_configured()


def test_smtp_outbound_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAIL_INTAKE_SMTP_OUTBOUND_ENABLED", raising=False)
    cfg = load_email_intake_config()
    assert cfg.smtp_outbound_enabled is False
