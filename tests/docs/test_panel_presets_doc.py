"""Keep the panel preset docs in step with the presets that actually ship (#2511)."""

from pathlib import Path

import pytest

from backend.staff.panel_models import PANEL_PRESETS

ROOT = Path(__file__).resolve().parents[2]
STAFF_HUB = ROOT / "docs/staff-hub.md"
OPERATING = ROOT / "docs/operating-the-fleet-through-the-dashboard.md"

#: Presets that pin real provider/model seats; operators need them documented.
PINNED = [p for p in PANEL_PRESETS if any("provider" in seat for seat in p["experts"])]


def test_fleet_decision_preset_ships_pinned() -> None:
    assert "fleet-decision" in {p["id"] for p in PINNED}


@pytest.mark.parametrize("preset", PINNED, ids=lambda p: p["id"])
def test_staff_hub_documents_each_pinned_preset(preset: dict) -> None:
    text = STAFF_HUB.read_text(encoding="utf-8")
    assert f"`{preset['id']}`" in text
    assert "/api/v1/staff/panels/presets" in text
    for seat in preset["experts"]:
        assert seat["name"] in text


@pytest.mark.parametrize("path", [STAFF_HUB, OPERATING], ids=lambda p: p.name)
def test_docs_do_not_call_a_shipped_preset_unmerged(path: Path) -> None:
    # A bullet or paragraph is one statement even when it wraps over lines.
    text = path.read_text(encoding="utf-8").replace("\n- ", "\n\n- ")
    for block in text.split("\n\n"):
        if "fleet-decision" in block:
            assert "not merged" not in " ".join(block.lower().split()), block
