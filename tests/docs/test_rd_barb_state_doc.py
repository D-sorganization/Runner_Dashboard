"""Contract tests for docs/agents/rd-barb-state.md scheduled passes (#4050)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "docs" / "agents" / "rd-barb-state.md"


def test_rd_barb_state_1933_pass_marks_holds_and_3884_reminder() -> None:
    text = STATE.read_text(encoding="utf-8")
    assert "2026-11-04 19:33" in text
    assert "RD #2657" in text
    assert "#3884" in text
    assert "72h" in text


def test_rd_barb_state_records_cloud_worker_rd_api_unreachable() -> None:
    text = STATE.read_text(encoding="utf-8")
    assert "8321" in text or "unreachable" in text.lower()
    assert "UNVERIFIED" in text
