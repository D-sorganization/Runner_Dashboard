"""rd-barb email channel: stdlib SMTP, TLS required, config-only, no secret in any error or log (RD #3075)."""

from __future__ import annotations

import logging
import smtplib
import ssl
from pathlib import Path
from typing import Any

import pytest
from staff.rd_barb_notify_health import NotifyHealth

from scripts import rd_barb_direct_notify as dn
from scripts import rd_barb_notify_email as em

T0 = 1_800_000_000.0
PASSWORD = "s3cret-pass-value"
CFG = {
    "RD_BARB_DIRECT_CHANNEL": "email",
    "RD_BARB_EMAIL_HOST": "smtp.example.com",
    "RD_BARB_EMAIL_FROM": "barb@example.com",
    "RD_BARB_EMAIL_TO": "owner@example.com",
    "RD_BARB_EMAIL_USER": "barb-user",
}
ENV = {"RD_BARB_EMAIL_PASSWORD": PASSWORD}
QUESTION = {
    "kind": "question",
    "summary": "Re-arm RD #1?",
    "body": "Checks green.",
    "urgency": "normal",
    "dedupe_key": "q-1",
    "options": [{"label": "Re-arm", "recommended": True}, {"label": "Wait"}],
    "recommendation_reason": "Required checks are green.",
}


class FakeSMTP:
    """Records the calls; ``fail`` names the step that raises ``error``."""

    instances: list[FakeSMTP] = []
    error: Exception | None = None
    fail = "send_message"

    def __init__(self, host: str, port: int, **kwargs: Any) -> None:
        self.args, self.kwargs = (host, port), kwargs
        self.calls: list[str] = []
        self.sent: list[Any] = []
        type(self).instances.append(self)
        self._maybe_fail("connect")

    def _maybe_fail(self, step: str) -> None:
        error = type(self).error
        if error is not None and type(self).fail == step:
            raise error

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self, **kwargs: Any) -> None:
        self.calls.append("starttls")
        self._maybe_fail("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login:{user}")
        self._maybe_fail("login")

    def send_message(self, message: Any) -> None:
        self.calls.append("send_message")
        self._maybe_fail("send_message")
        self.sent.append(message)


class FakeSMTPSSL(FakeSMTP):
    instances: list[FakeSMTP] = []


@pytest.fixture(autouse=True)
def _reset() -> None:
    for cls in (FakeSMTP, FakeSMTPSSL):
        cls.instances, cls.error, cls.fail = [], None, "send_message"


def _channel(cfg: dict[str, str] | None = None, env: dict[str, str] | None = None) -> em.EmailChannel:
    return em.email_channel(
        CFG if cfg is None else cfg,
        error_cls=dn.ChannelError,
        environ=ENV if env is None else env,
        smtp=FakeSMTP,
        smtp_ssl=FakeSMTPSSL,
    )


def _code(channel: em.EmailChannel, msg: dict[str, Any] = QUESTION) -> str:
    with pytest.raises(dn.ChannelError) as info:
        channel.send(msg)
    return info.value.code


def test_sends_one_message_with_headers_and_envelope_text() -> None:
    _channel().send(QUESTION)
    (conn,) = FakeSMTP.instances
    (message,) = conn.sent
    assert message["From"] == "barb@example.com" and message["To"] == "owner@example.com"
    assert message["Subject"] == "[rd-barb question] Re-arm RD #1?"
    text = message.get_content()
    assert "Checks green." in text and "q-1" in text and "Re-arm (recommended)" in text


def test_port_587_default_uses_starttls_before_login() -> None:
    _channel().send(QUESTION)
    (conn,) = FakeSMTP.instances
    assert conn.args == ("smtp.example.com", 587) and conn.kwargs["timeout"] == em.SMTP_TIMEOUT_S
    assert conn.calls == ["starttls", "login:barb-user", "send_message"]


def test_port_465_uses_implicit_tls_and_no_starttls() -> None:
    _channel({**CFG, "RD_BARB_EMAIL_PORT": "465"}).send(QUESTION)
    assert FakeSMTP.instances == [] and len(FakeSMTPSSL.instances) == 1
    assert FakeSMTPSSL.instances[0].calls == ["login:barb-user", "send_message"]


@pytest.mark.parametrize("drop", sorted(em.REQUIRED_KEYS))
def test_missing_required_config_fails_closed(drop: str) -> None:
    cfg = {k: v for k, v in CFG.items() if k != drop}
    assert _code(_channel(cfg)) == "email_not_configured" and FakeSMTP.instances == []


def test_missing_password_env_and_bad_port_fail_closed() -> None:
    assert _code(_channel(env={})) == "email_not_configured"
    assert _code(_channel({**CFG, "RD_BARB_EMAIL_PORT": "abc"})) == "email_not_configured"


def test_password_env_name_is_configurable() -> None:
    cfg = {**CFG, "RD_BARB_EMAIL_PASSWORD_ENV": "MY_SMTP_SECRET"}
    assert _code(_channel(cfg)) == "email_not_configured"
    _channel(cfg, env={"MY_SMTP_SECRET": PASSWORD}).send(QUESTION)


@pytest.mark.parametrize(
    ("error", "fail", "code"),
    [
        (smtplib.SMTPAuthenticationError(535, b"bad creds " + PASSWORD.encode()), "login", "email_auth_failed"),
        (TimeoutError("owner@example.com timed out"), "connect", "email_timeout"),
        (ssl.SSLError("handshake smtp.example.com"), "starttls", "email_tls_failed"),
        (smtplib.SMTPNotSupportedError("no STARTTLS"), "starttls", "email_tls_unsupported"),
        (smtplib.SMTPRecipientsRefused({"owner@example.com": (550, b"no")}), "send_message", "email_rejected"),
        (smtplib.SMTPServerDisconnected("gone"), "send_message", "email_smtp_error"),
        (ConnectionRefusedError("smtp.example.com"), "connect", "email_connect_failed"),
    ],
)
def test_errors_map_to_codes_by_type_only(error: Exception, fail: str, code: str) -> None:
    FakeSMTP.error, FakeSMTP.fail = error, fail
    assert _code(_channel()) == code


def test_password_never_in_errors_or_logs(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    FakeSMTP.error, FakeSMTP.fail = smtplib.SMTPAuthenticationError(535, PASSWORD.encode()), "login"
    health = NotifyHealth(tmp_path / "health.json")
    with caplog.at_level(logging.DEBUG):
        result = dn.deliver(QUESTION, _channel(), health, clock=lambda: T0)
    blob = repr(result) + caplog.text + (tmp_path / "health.json").read_text(encoding="utf-8")
    assert PASSWORD not in blob and "owner@example.com" not in blob and "smtp.example.com" not in blob


def test_registered_channel_records_health_ok_and_failing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)
    monkeypatch.setenv("RD_BARB_EMAIL_PASSWORD", PASSWORD)
    health = NotifyHealth(tmp_path / "health.json")
    channel = dn.CHANNELS["email"](CFG)
    assert dn.deliver(QUESTION, channel, health, clock=lambda: T0)["health"] == "ok"
    FakeSMTP.error = smtplib.SMTPAuthenticationError(535, b"x")
    FakeSMTP.fail = "login"
    result = dn.deliver(QUESTION, channel, health, clock=lambda: T0 + 1)
    assert result["error"] == "email_auth_failed" and result["health"] == "failing"
    assert health.status(now=T0 + 1)["channel"] == "email"
