"""Staff adapter ids remain compatible with the canonical provider registry."""

from __future__ import annotations

import pytest
from agent_remediation.provider_registry import PROVIDER_REGISTRY, by_dashboard_id, canonical_provider_id
from staff.adapters import ADAPTERS, get_adapter


def test_staff_ids_resolve_to_canonical_registry_entries() -> None:
    registry_ids = {entry.dashboard_id for entry in PROVIDER_REGISTRY}

    assert {provider_id: canonical_provider_id(provider_id) for provider_id in ADAPTERS} == {
        "claude": "claude_code_cli",
        "anthropic-api": "anthropic_api",
        "codex": "codex_cli",
        "antigravity": "antigravity",
        "gemini": "gemini_cli",
        "cursor-agent": "cursor_agent",
        "ollama": "ollama",
        "claude-ollama": "ollama",
    }
    assert all(canonical_provider_id(provider_id) in registry_ids for provider_id in ADAPTERS)
    for provider_id, adapter in ADAPTERS.items():
        entry = by_dashboard_id()[canonical_provider_id(provider_id)]
        if adapter.models:
            assert adapter.models == entry.models


def test_canonical_ids_and_historical_spellings_share_one_identity() -> None:
    assert canonical_provider_id("claude_code_cli") == "claude_code_cli"
    assert canonical_provider_id("claude-cli") == "claude_code_cli"
    assert canonical_provider_id("codex-cli") == "codex_cli"
    assert canonical_provider_id("gemini-cli") == "gemini_cli"
    assert canonical_provider_id("cursor_agent") == "cursor_agent"
    assert canonical_provider_id("agy") == "antigravity"
    assert canonical_provider_id("ollama-local") == "ollama"
    assert get_adapter("claude_code_cli") is ADAPTERS["claude"]
    assert get_adapter("codex-cli") is ADAPTERS["codex"]


@pytest.mark.parametrize("provider_id", ["", "unknown-provider"])
def test_unknown_provider_ids_fail_closed(provider_id: str) -> None:
    with pytest.raises(KeyError, match="provider id|unknown provider"):
        canonical_provider_id(provider_id)


def test_anthropic_api_adapter_ships_inert_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """#3534: the failover step must report not-installed until Dieter configures a key."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY_ENV_VAR", raising=False)
    adapter = ADAPTERS["anthropic-api"]
    assert adapter.installed() is False


def test_anthropic_api_adapter_reports_installed_once_a_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")  # pragma: allowlist secret
    adapter = ADAPTERS["anthropic-api"]
    assert adapter.installed() is True
    assert adapter.executable == "claude"
    assert adapter.build_command("do it", "/tmp/wt", model=None)[0] == "claude"
