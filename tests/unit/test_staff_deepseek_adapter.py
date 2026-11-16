"""DeepSeek's paid route must be explicit and isolated from other providers."""

from types import SimpleNamespace

import pytest
from staff.adapters import ADAPTERS
from staff.cloud_dispatch import scrub_api_key_env
from staff.deepseek_provider import DeepSeekNotEnabled, deepseek_environment
from staff.panel_models import PanelCreateRequest, PanelSpeaker
from staff.panel_subscription_policy import check_provider_subscription


def test_route_fails_closed_without_explicit_opt_in() -> None:
    with pytest.raises(DeepSeekNotEnabled):
        deepseek_environment({"DEEPSEEK_API_KEY": "example"})  # pragma: allowlist secret


def test_route_requires_its_own_credential() -> None:
    with pytest.raises(DeepSeekNotEnabled):
        deepseek_environment({"STAFF_DEEPSEEK_API_ENABLED": "true", "ANTHROPIC_API_KEY": "example"})  # noqa: E501 # pragma: allowlist secret


def test_route_builds_only_deepseek_provider_environment() -> None:
    env = deepseek_environment({"STAFF_DEEPSEEK_API_ENABLED": "true", "DEEPSEEK_API_KEY": "example"})  # noqa: E501 # pragma: allowlist secret
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "example"
    assert env["ANTHROPIC_API_KEY"] == ""


def test_deepseek_credential_does_not_reach_other_or_cloud_launches() -> None:
    assert "DEEPSEEK_API_KEY" not in scrub_api_key_env({"DEEPSEEK_API_KEY": "example"})  # pragma: allowlist secret


def test_subscription_policy_refuses_paid_provider() -> None:
    assert not check_provider_subscription("deepseek-api").ok


def test_adapter_is_chat_only_and_uses_deepseek_model() -> None:
    adapter = ADAPTERS["deepseek-api"]
    assert not adapter.unattended
    argv = adapter.chat_argv("hello", ".")
    assert argv[-2:] == ["--model", "deepseek-flash"]
    assert argv[argv.index("--tools") + 1] == ""


def test_paid_panel_requires_explicit_confirmation() -> None:
    experts = [{"name": name, "perspective": "review", "provider": "claude"} for name in ("A", "B", "C")]
    with pytest.raises(ValueError, match="confirm_cost"):
        PanelCreateRequest(topic="review", experts=experts, moderator_provider="deepseek-api")
    assert PanelCreateRequest(topic="review", experts=experts, moderator_provider="deepseek-api", confirm_cost=True)
    experts[0]["provider"] = "deepseek-api"
    with pytest.raises(ValueError, match="confirm_cost"):
        PanelCreateRequest(topic="review", experts=experts)


def test_unknown_model_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported DeepSeek model"):
        ADAPTERS["deepseek-api"].chat_argv("hello", ".", model="claude-opus")


@pytest.mark.asyncio
async def test_panel_invokes_mocked_claude_with_isolated_deepseek_environment(monkeypatch, tmp_path) -> None:
    from staff import panel

    monkeypatch.setenv("STAFF_DEEPSEEK_API_ENABLED", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "example")  # pragma: allowlist secret
    monkeypatch.setenv("OPENAI_API_KEY", "other-example")  # pragma: allowlist secret
    monkeypatch.setattr(panel.provider_switch, "is_disabled", lambda _: False)
    monkeypatch.setattr(panel, "prepare_panel_turn_scratch", lambda: str(tmp_path))
    monkeypatch.setattr(panel, "remove_panel_turn_scratch", lambda _: None)
    monkeypatch.setattr(panel.cli_projects, "remove_turn_project", lambda *_: None)
    captured = {}
    process = SimpleNamespace(poll=lambda: 0)

    def spawn(**kwargs):
        captured.update(kwargs)
        return process

    async def stream(**_kwargs):
        return SimpleNamespace(reply_text="review example", returncode=0, stderr_lines=[])

    monkeypatch.setattr(panel, "spawn_cli_process", spawn)
    monkeypatch.setattr(panel, "stream_turn_output", stream)
    result = await panel.default_turn_runner(PanelSpeaker("A", "review", "deepseek-api", None), "question", "t", None)
    assert result.ok and result.text == "review [redacted]"
    assert captured["env"]["ANTHROPIC_AUTH_TOKEN"] == "example"
    assert "DEEPSEEK_API_KEY" not in captured["env"]
    assert "OPENAI_API_KEY" not in captured["env"]
    assert captured["cmd"][-2:] == ["--model", "deepseek-flash"]


@pytest.mark.asyncio
async def test_unconfigured_panel_never_spawns_a_provider(monkeypatch, tmp_path) -> None:
    from staff import panel

    monkeypatch.delenv("STAFF_DEEPSEEK_API_ENABLED", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(panel.provider_switch, "is_disabled", lambda _: False)
    monkeypatch.setattr(panel, "prepare_panel_turn_scratch", lambda: str(tmp_path))
    monkeypatch.setattr(panel, "remove_panel_turn_scratch", lambda _: None)
    monkeypatch.setattr(panel.cli_projects, "remove_turn_project", lambda *_: None)
    monkeypatch.setattr(panel, "spawn_cli_process", lambda **_: pytest.fail("provider spawned"))
    with pytest.raises(DeepSeekNotEnabled):
        await panel.default_turn_runner(PanelSpeaker("A", "review", "deepseek-api", None), "question", "t", None)
