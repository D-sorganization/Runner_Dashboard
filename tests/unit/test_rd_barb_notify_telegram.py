"""rd-barb Telegram channel: urgent-only filter, scrubbed rendering, rate-limit digest, secret hygiene (RD #3076)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from staff.rd_barb_notify_health import NotifyHealth

from scripts import rd_barb_direct_notify as dn
from scripts import rd_barb_notify_telegram as tg

T0 = 1_800_000_000.0
QUESTION = {
    "kind": "question",
    "summary": "Re-arm RD #1?",
    "importance": "urgent",
    "dedupe_key": "q-1",
    "correlation_id": "c_deadbeef",
    "options": [{"label": "Re-arm", "recommended": True}, {"label": "Wait"}],
    "recommendation_reason": "Required checks are green.",
}
ALERT = {
    "kind": "alert",
    "summary": "Runner pool exhausted",
    "importance": "urgent",
    "dedupe_key": "a-1",
    "recommended_action": "Scale node-b +2",
}
CFG = {"RD_BARB_TELEGRAM": "on", "RD_BARB_TELEGRAM_CHAT_ID": "999"}
ENV = {"TELEGRAM_BOT_TOKEN": "s3cret-bot-token"}  # pragma: allowlist secret


@pytest.fixture
def health(tmp_path: Path) -> NotifyHealth:
    return NotifyHealth(tmp_path / "health.json")


def _channel(cfg: dict[str, str] | None = None, **kwargs: Any) -> tg.TelegramBotApiChannel:
    sent: list[tuple[str, dict[str, Any]]] = kwargs.pop("sent", [])
    return tg.telegram_channel(
        cfg or CFG,
        error_cls=dn.ChannelError,
        environ=ENV,
        http_post=lambda url, payload: sent.append((url, payload)),
        **kwargs,
    )


@pytest.mark.parametrize(
    ("kind", "importance", "expected"),
    [
        ("question", "urgent", True),
        ("alert", "urgent", True),
        ("update", "urgent", False),
        ("question", "normal", False),
        ("question", "high", False),
    ],
)
def test_is_urgent_filters_kind_and_importance(kind: str, importance: str, expected: bool) -> None:
    assert tg.is_urgent({"kind": kind, "importance": importance}) is expected


@pytest.mark.parametrize(
    ("update", "error"),
    [
        ({**QUESTION, "importance": "normal"}, "not_urgent"),
        ({**QUESTION, "options": [{"label": "Re-arm"}, {"label": "Wait"}]}, None),
    ],
)
def test_bad_envelope_is_rejected_before_any_send(
    health: NotifyHealth, update: dict[str, Any], error: str | None
) -> None:
    sent: list[Any] = []
    result = dn.deliver(update, _channel(sent=sent), health, clock=lambda: T0)
    assert result["ok"] is False and sent == [] and (error is None or result["error"] == error)


def test_render_marks_recommended_option_and_includes_correlation_id() -> None:
    text = tg.render_message(QUESTION)
    assert "Re-arm (Recommended)" in text and "- Wait" in text and "Ref: c_deadbeef" in text


def test_render_scrubs_tokens_and_private_hosts() -> None:
    leaky = {**ALERT, "summary": "ghp_" + "a" * 36 + " on host 10.0.0.5"}
    rendered = tg.render_message(leaky)
    assert "ghp_" not in rendered and "10.0.0.5" not in rendered


def test_successful_send_posts_rendered_text_to_bot_api(health: NotifyHealth) -> None:
    sent: list[tuple[str, dict[str, Any]]] = []
    channel = _channel(sent=sent)
    result = dn.deliver(QUESTION, channel, health, clock=lambda: T0)
    assert result == {"ok": True, "status": "sent", "health": "ok"}
    [(url, payload)] = sent
    assert url == "https://api.telegram.org/bots3cret-bot-token/sendMessage" and payload["chat_id"] == "999"


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        ({**CFG, "RD_BARB_TELEGRAM": "off"}, "telegram_disabled"),
        ({"RD_BARB_TELEGRAM": "on"}, "telegram_not_configured"),
    ],
)
def test_kill_switch_and_missing_config_fail_closed(health: NotifyHealth, cfg: dict[str, str], expected: str) -> None:
    sent: list[Any] = []
    result = dn.deliver(QUESTION, _channel(cfg, sent=sent), health, clock=lambda: T0)
    assert result["error"] == expected and sent == []


def test_token_is_never_recorded_in_health(health: NotifyHealth, tmp_path: Path) -> None:
    channel = _channel(sent=[])
    dn.deliver(QUESTION, channel, health, clock=lambda: T0)
    stored = (tmp_path / "health.json").read_text(encoding="utf-8")
    assert "s3cret-bot-token" not in stored


def test_rate_limiter_folds_overflow_into_one_digest_and_resets_after_the_window() -> None:
    limiter = tg.RateLimiter(max_per_hour=2)
    assert limiter.allow(T0) and limiter.allow(T0)
    assert not limiter.allow(T0) and not limiter.allow(T0)
    assert limiter.drain_digest() == 2
    assert limiter.drain_digest() == 0
    assert limiter.allow(T0 + tg.RATE_WINDOW_S + 1)


def test_rate_limited_send_is_recorded_as_folded_not_delivered(health: NotifyHealth) -> None:
    limiter = tg.RateLimiter(max_per_hour=1)
    first = _channel(rate_limiter=limiter)
    second = _channel(rate_limiter=limiter)
    assert dn.deliver(QUESTION, first, health, clock=lambda: T0)["ok"] is True
    result = dn.deliver(ALERT, second, health, clock=lambda: T0)
    assert result["error"] == "rate_limited_folded_into_digest"


@pytest.mark.parametrize(
    ("provider", "channels_enabled", "expected"),
    [("claude", True, "plugin"), ("claude", False, "bot_api"), ("codex", True, "bot_api"), ("agy", False, "bot_api")],
)
def test_select_path_picks_exactly_one_inbound_owner(provider: str, channels_enabled: bool, expected: str) -> None:
    assert tg.select_path(provider, channels_enabled=channels_enabled) == expected


def test_permission_relay_is_off_on_the_plugin_path() -> None:
    assert tg.CLAUDE_CHANNELS_TELEGRAM_CONFIG["permission_relay"] is False
    assert tg.CLAUDE_CHANNELS_TELEGRAM_CONFIG["sender_allowlist"] == "allowlist"


def test_telegram_registered_in_the_shared_channel_registry() -> None:
    channel = dn.channel_from_config({**CFG, "RD_BARB_DIRECT_CHANNEL": "telegram"})
    assert channel.name == "telegram"


def test_dry_run_cli_renders_without_sending(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io
    import json

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(QUESTION)))
    assert tg.main(["--json", "-", "--dry-run"]) == 0
    assert "Re-arm (Recommended)" in capsys.readouterr().out
