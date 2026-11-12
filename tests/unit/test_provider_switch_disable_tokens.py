"""Disable tokens accept 1/true/yes/on case-insensitively (#4267)."""

from __future__ import annotations

import pytest
import provider_switch

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("value", ["1", "TRUE", "Yes", "ON", "on"])
def test_disable_flag_tokens(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", f"gemini={value}")
    assert provider_switch.is_disabled("gemini")


def test_bare_provider_name_still_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "codex")
    assert provider_switch.is_disabled("codex")
