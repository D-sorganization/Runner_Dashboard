"""Staff adapter ids remain compatible with the canonical provider registry."""

from __future__ import annotations

import pytest
from agent_remediation.provider_registry import (
    PROVIDER_REGISTRY,
    by_dashboard_id,
    canonical_provider_id,
    cloud_mutation_allowed,
)
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


def test_cursor_agent_is_registered_non_mutating_for_cloud_dispatch() -> None:
    """#4081: Cursor cloud agents are a hard-gated, non-mutating lane (ADR-0007)."""
    entry = by_dashboard_id()["cursor_agent"]
    assert entry.cloud_mutation_allowed is False
    assert cloud_mutation_allowed("cursor-agent") is False
    assert cloud_mutation_allowed("cursor_agent") is False


@pytest.mark.parametrize("provider_id", ["claude", "codex", "claude_code_cli", "codex_cli"])
def test_other_providers_default_to_cloud_mutation_allowed(provider_id: str) -> None:
    assert cloud_mutation_allowed(provider_id) is True


def test_cloud_mutation_allowed_fails_open_for_an_unregistered_id() -> None:
    """No registry row to consult means no gate recorded here; closed-world ids are
    enforced separately by :func:`canonical_provider_id`."""
    assert cloud_mutation_allowed("jules") is True


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


def test_grok_cli_is_registered_as_its_own_provider() -> None:
    """#4130: the standalone Grok CLI gets a canonical row, distinct from the
    Cursor-hosted Grok models under ``cursor_agent``."""
    registry_ids = {entry.dashboard_id for entry in PROVIDER_REGISTRY}
    assert "grok_cli" in registry_ids

    entry = by_dashboard_id()["grok_cli"]
    assert entry.conductor_id == "grok-cli"
    assert entry.availability_probe == ("grok",)
    # Ships inert: no staff launch adapter exists yet (session spawn parity
    # lands in the sibling issue that depends on this registry row).
    assert entry.enabled is False


def test_grok_aliases_resolve_to_the_standalone_grok_cli_row() -> None:
    assert canonical_provider_id("grok") == "grok_cli"
    assert canonical_provider_id("grok-cli") == "grok_cli"


def test_grok_cli_is_distinct_from_cursor_hosted_grok_models() -> None:
    """Cursor Agent still reaches Grok models through the Cursor subscription;
    that is a separate row/path from the standalone ``grok_cli`` CLI."""
    cursor_entry = by_dashboard_id()["cursor_agent"]
    grok_entry = by_dashboard_id()["grok_cli"]
    assert grok_entry.dashboard_id not in cursor_entry.models
    assert any(model.startswith("grok-") for model in cursor_entry.models)
