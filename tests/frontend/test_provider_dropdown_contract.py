"""UI contract: global provider pickers must not list registry-disabled providers."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_provider_model_selector_uses_selectable_providers_only() -> None:
    """The shell dropdown must read ``selectableProviders``, not the full registry."""
    src = (ROOT / "frontend/src/primitives/ProviderModelSelector.tsx").read_text(encoding="utf-8")
    assert "registry.selectableProviders" in src
    assert "registry.providers.map" not in src


@pytest.mark.unit
def test_selectable_ids_match_backend_registry() -> None:
    from agent_remediation.provider_registry import selectable_conductor_ids  # noqa: PLC0415

    hook_src = (ROOT / "frontend/src/lib/useProviderRegistry.ts").read_text(encoding="utf-8")
    assert "selectableProviders" in hook_src
    disabled = {"jules-cli", "jules-api"}
    assert disabled.isdisjoint(selectable_conductor_ids())
