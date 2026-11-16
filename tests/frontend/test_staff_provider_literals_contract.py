"""Staff UI provider literals must not disagree with backend panel/registry rows (#4267 G2-11)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_new_panel_form_fallback_providers_are_panel_capable() -> None:
    """``DEFAULT_PROVIDERS`` is only a presets-API fallback; ids must be panel-capable staff providers."""
    from staff.panel_models import PANEL_PROVIDERS  # noqa: PLC0415

    src = (ROOT / "frontend/src/pages/StaffConsole/NewPanelForm.tsx").read_text(encoding="utf-8")
    match = re.search(r"const DEFAULT_PROVIDERS = (\[[^\]]+\]);", src)
    assert match, "NewPanelForm must keep DEFAULT_PROVIDERS documented for API-failure fallback"
    fallback_ids = json.loads(match.group(1).replace("'", '"'))
    assert set(fallback_ids) <= set(PANEL_PROVIDERS), (
        f"fallback providers {fallback_ids} are not all in backend PANEL_PROVIDERS"
    )


@pytest.mark.unit
def test_new_panel_form_loads_presets_providers_from_api() -> None:
    src = (ROOT / "frontend/src/pages/StaffConsole/NewPanelForm.tsx").read_text(encoding="utf-8")
    assert "fetchPanelPresets" in src
    assert "data.providers" in src
