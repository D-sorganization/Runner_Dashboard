from pathlib import Path

from email_intake.policy import MailPolicyError, parse_mail_policy_yaml


def test_parse_companion_policy() -> None:
    text = Path("docs/email-intake/companion/staff-mail_policy.yml").read_text(encoding="utf-8")
    policy = parse_mail_policy_yaml(text)
    assert policy.require_dmarc_pass is True
    assert policy.sender_enabled("fleet-operator@example.com")
    assert not policy.sender_enabled("dolson@inentec.com")


def test_rejects_bad_sender() -> None:
    try:
        parse_mail_policy_yaml("allowed_senders:\n  - address: not-an-email\n")
    except MailPolicyError:
        return
    raise AssertionError("expected MailPolicyError")
