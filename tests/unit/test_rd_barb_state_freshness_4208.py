"""rd-barb state ledger must record the 4208–4220 GM-404 freshness batch (#4208)."""

from __future__ import annotations

from pathlib import Path

import pytest

STATE_PATH = Path(__file__).resolve().parents[2] / "docs/agents/rd-barb-state.md"
# Scheduled rd-barb state-file passes (#4208–#4220); code fixes are #4212/#4214 and #4216/#4217.
FRESHNESS_PASS_ISSUES = (4208, 4209, 4210, 4211, 4213, 4215, 4218, 4219, 4220)


@pytest.mark.unit
def test_state_ledger_records_4208_through_4220_freshness_batch() -> None:
    """Each scheduled pass issue in the batch must land in the live ledger (never delete rows)."""
    text = STATE_PATH.read_text(encoding="utf-8")
    ledger_start = text.find("## Ledger")
    assert ledger_start != -1, "rd-barb-state.md must keep a ## Ledger section"
    ledger = text[ledger_start:]
    missing = [n for n in FRESHNESS_PASS_ISSUES if f"#{n}" not in ledger and f"RD #{n}" not in ledger]
    assert not missing, f"ledger missing freshness pass issues: {missing}"


@pytest.mark.unit
def test_state_ledger_reconciles_gm_404_with_live_fleet_app_check() -> None:
    """When issues document GM 404, the ledger must say so and recommend owner App-scope check."""
    ledger = STATE_PATH.read_text(encoding="utf-8")
    assert "Gasification_Model" in ledger
    assert "404" in ledger
    assert "4208" in ledger or "#4208" in ledger
    assert "installation" in ledger.lower() or "App" in ledger