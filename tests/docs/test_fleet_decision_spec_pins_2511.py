"""Issue #2511: pinned fleet-decision seat models match panel preset (#2511 spec)."""

from __future__ import annotations

import re
from pathlib import Path

from backend.staff.panel_models import PANEL_PRESETS

SPEC = Path(__file__).resolve().parents[2] / "docs/agents/issue-specs/2511-fleet-decision-preset.md"


def _spec_models() -> dict[str, tuple[str, str]]:
    text = SPEC.read_text(encoding="utf-8")
    rows = re.findall(
        r"\|\s*(Argon|Astra|Fable)\s*\|\s*(\w+)\s*\|\s*`([^`]+)`\s*\|",
        text,
    )
    return {name: (provider, model) for name, provider, model in rows}


def test_fleet_decision_models_match_issue_spec_pins() -> None:
    spec = _spec_models()
    fleet = next(p for p in PANEL_PRESETS if p["id"] == "fleet-decision")
    live = {e["name"]: (e["provider"], e["model"]) for e in fleet["experts"]}
    assert set(spec) == set(live)
    for seat, pair in spec.items():
        assert live[seat] == pair
