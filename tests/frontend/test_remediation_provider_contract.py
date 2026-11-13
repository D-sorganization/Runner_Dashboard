"""Remediation pickers must not hardcode provider order or list disabled registry rows (#2715)."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_remediation_modules_do_not_hardcode_jules_provider_lists() -> None:
    paths = [
        ROOT / "frontend/src/pages/RemediationTab.tsx",
        ROOT / "frontend/src/pages/RemediationPage.tsx",
        ROOT / "frontend/src/pages/RemediationIssues.tsx",
        ROOT / "frontend/src/pages/RemediationPRs.tsx",
        ROOT / "frontend/src/pages/Remediation/mobileTypes.ts",
        ROOT / "frontend/src/lib/providerModels.ts",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "jules_api" not in text, f"{path.name} still references jules_api"
        assert "DEFAULT_PROVIDER_ORDER" not in text, f"{path.name} still has DEFAULT_PROVIDER_ORDER"
        assert "PROVIDER_OPTIONS" not in text, f"{path.name} still has PROVIDER_OPTIONS"


@pytest.mark.unit
def test_remediation_ui_uses_selectable_providers() -> None:
    for rel in (
        "frontend/src/pages/RemediationIssues.tsx",
        "frontend/src/pages/RemediationPRs.tsx",
        "frontend/src/lib/remediationProviders.ts",
    ):
        assert "selectableProviders" in (ROOT / rel).read_text(encoding="utf-8")
