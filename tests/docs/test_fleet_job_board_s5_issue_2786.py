"""Issue #2786 closeout evidence: Job board panel shipped on main (S5)."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PANEL = REPO / "frontend/src/pages/FleetCommand/JobBoardPanel.tsx"
PAGE = REPO / "frontend/src/pages/FleetCommand/FleetCommandPage.tsx"
TESTS = REPO / "frontend/src/pages/__tests__/FleetCommandOps.test.tsx"

ISSUE_TESTS = (
    "renders jobs and filters by repo and priority",
    "shows degraded banner when unavailable or incomplete",
    "shows 409 reason when a claim loses",
)


def test_job_board_panel_module_exists() -> None:
    assert PANEL.is_file()


def test_fleet_command_page_wires_job_board_panel() -> None:
    text = PAGE.read_text(encoding="utf-8")
    assert "JobBoardPanel" in text


def test_acceptance_tests_present_in_fleet_command_ops_suite() -> None:
    text = TESTS.read_text(encoding="utf-8")
    for needle in ISSUE_TESTS:
        assert needle in text, needle
