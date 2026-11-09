"""Fail-closed subscription-only panel policy (#4223).

- A disabled provider or one with no live OAuth session fails the check closed, never a key check.
- ``verify_panel_subscriptions`` reports every expert and the moderator, naming only the failures.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import staff.panel_subscription_policy as policy
from staff.panel_models import MODERATOR_NAME, PanelCreateRequest

EXPERTS = [
    {"name": "Ada", "perspective": "numerical methods", "provider": "claude"},
    {"name": "Brook", "perspective": "experimental physics", "provider": "codex"},
    {"name": "Cy", "perspective": "software architecture", "provider": "antigravity"},
]


@pytest.mark.unit
@pytest.mark.parametrize(
    "provider", ["anthropic-api", "deepseek-api", "cursor-agent", "antigravity", "unknown-provider"]
)
def test_unverified_providers_fail_closed_without_probing(provider: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy, "provider_oauth_session_alive", lambda _p: pytest.fail("unexpected probe"))
    assert not policy.check_provider_subscription(provider).ok


@pytest.mark.unit
@pytest.mark.parametrize("variable", ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"])
def test_claude_with_api_overrides_is_not_subscription_verified(variable: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(variable, "synthetic")  # pragma: allowlist secret
    monkeypatch.setattr(policy, "provider_oauth_session_alive", lambda _p: pytest.fail("unexpected probe"))
    assert not policy.check_provider_subscription("claude").ok


@pytest.mark.unit
def test_claude_profile_with_api_key_helper_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "settings.json").write_text('{"apiKeyHelper":"helper"}', encoding="utf-8")  # pragma: allowlist secret
    monkeypatch.setattr(policy, "provider_oauth_session_alive", lambda _p: pytest.fail("unexpected probe"))
    assert not policy.check_provider_subscription("claude").ok


@pytest.mark.unit
def test_codex_api_key_auth_file_is_not_subscription_verified(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text('{"auth_mode":"apikey"}', encoding="utf-8")
    monkeypatch.setattr(policy, "provider_oauth_session_alive", lambda _p: pytest.fail("unexpected probe"))
    assert not policy.check_provider_subscription("codex").ok


@pytest.mark.unit
def test_codex_custom_provider_is_not_subscription_verified(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text(
        '{"auth_mode":"chatgpt","tokens":{"access_token":"fake"}}',  # pragma: allowlist secret
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text('model_provider = "custom"', encoding="utf-8")
    monkeypatch.setattr(policy, "provider_oauth_session_alive", lambda _p: pytest.fail("unexpected probe"))
    assert not policy.check_provider_subscription("codex").ok


@pytest.mark.unit
def test_codex_chatgpt_auth_can_be_verified(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text(
        '{"auth_mode":"chatgpt","tokens":{"access_token":"fake"}}',  # pragma: allowlist secret
        encoding="utf-8",
    )
    monkeypatch.setattr(policy, "provider_oauth_session_alive", lambda _p: (True, ""))
    monkeypatch.setattr(
        policy.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="Logged in using ChatGPT", stderr=""),
    )
    assert policy.check_provider_subscription("codex").ok


@pytest.mark.unit
def test_codex_status_failure_is_not_subscription_verified(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text(
        '{"auth_mode":"chatgpt","tokens":{"access_token":"fake"}}',  # pragma: allowlist secret
        encoding="utf-8",
    )
    monkeypatch.setattr(
        policy.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=1, stdout="", stderr="login required")
    )
    assert not policy.check_provider_subscription("codex").ok


@pytest.mark.unit
@pytest.mark.parametrize("config", ['model_providers = "invalid"', 'profile = "custom"', "bad = ["])
def test_codex_ambiguous_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, config: str
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text(
        '{"auth_mode":"chatgpt","tokens":{"access_token":"fake"}}',  # pragma: allowlist secret
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text(config, encoding="utf-8")
    monkeypatch.setattr(policy, "provider_oauth_session_alive", lambda _p: pytest.fail("unexpected probe"))
    assert not policy.check_provider_subscription("codex").ok


def _request(**overrides: Any) -> PanelCreateRequest:
    data: dict[str, Any] = {"topic": "Use implicit stepping?", "experts": EXPERTS, "rounds": 1}
    data.update(overrides)
    return PanelCreateRequest.model_validate(data)


@pytest.mark.unit
def test_a_disabled_provider_fails_closed_without_checking_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "codex")
    monkeypatch.setattr(policy, "provider_oauth_session_alive", lambda _p: (False, "should never be called"))
    check = policy.check_provider_subscription("codex")
    assert check.ok is False
    assert "disabled on this node" in check.reason


@pytest.mark.unit
def test_an_enabled_provider_with_no_live_session_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAFF_DISABLED_PROVIDERS", raising=False)
    monkeypatch.setattr(policy, "_codex_subscription_auth", lambda: True)
    monkeypatch.setattr(policy, "provider_oauth_session_alive", lambda _p: (False, "no Codex auth.json"))
    check = policy.check_provider_subscription("codex")
    assert check.ok is False and check.reason == "no Codex auth.json"


@pytest.mark.unit
def test_a_live_subscription_session_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAFF_DISABLED_PROVIDERS", raising=False)
    monkeypatch.setattr(policy, "_claude_subscription_config", lambda: True)
    monkeypatch.setattr(policy, "provider_oauth_session_alive", lambda _p: (True, ""))
    check = policy.check_provider_subscription("claude")
    assert check.ok is True and check.reason == ""


@pytest.mark.unit
def test_panel_seats_lists_every_expert_then_the_moderator() -> None:
    seats = policy.panel_seats(_request(moderator_provider="antigravity"))
    assert seats == [("Ada", "claude"), ("Brook", "codex"), ("Cy", "antigravity"), (MODERATOR_NAME, "antigravity")]


@pytest.mark.unit
def test_verify_panel_subscriptions_names_only_the_failing_seats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAFF_DISABLED_PROVIDERS", raising=False)
    monkeypatch.setattr(policy, "_codex_subscription_auth", lambda: True)
    monkeypatch.setattr(policy, "_claude_subscription_config", lambda: True)

    def fake_alive(provider: str) -> tuple[bool, str]:
        return (provider != "codex", "" if provider != "codex" else "no Codex auth.json")

    monkeypatch.setattr(policy, "provider_oauth_session_alive", fake_alive)
    experts = [dict(e, provider="claude") if e["provider"] == "antigravity" else e for e in EXPERTS]
    failures = policy.verify_panel_subscriptions(_request(experts=experts))
    assert failures == [{"seat": "Brook", "provider": "codex", "reason": "no Codex auth.json"}]


@pytest.mark.unit
def test_verify_panel_subscriptions_is_empty_when_every_seat_is_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAFF_DISABLED_PROVIDERS", raising=False)
    monkeypatch.setattr(policy, "_codex_subscription_auth", lambda: True)
    monkeypatch.setattr(policy, "_claude_subscription_config", lambda: True)
    monkeypatch.setattr(policy, "provider_oauth_session_alive", lambda _p: (True, ""))
    experts = [dict(e, provider="claude") if e["provider"] == "antigravity" else e for e in EXPERTS]
    assert policy.verify_panel_subscriptions(_request(experts=experts)) == []
