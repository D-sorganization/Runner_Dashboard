"""The tooling capability matrix must match the handoff store, not conflate its columns (#4667)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "agents" / "tooling-capabilities-matrix.md"
STORE = ROOT / "backend" / "staff" / "barb_task_inventory.py"


def _handoff_section() -> str:
    text = DOC.read_text(encoding="utf-8")
    return text.split("## 2.", 1)[1].split("## 3.", 1)[0]


def test_handoff_section_separates_state_status_and_surface() -> None:
    section = _handoff_section()
    assert "status = needs_desktop" not in section
    assert "status = desktop_claimed" not in section
    assert "status = resume_ready" not in section
    assert "back to `headless_cli`" not in section
    assert "handoff_state" in section
    assert "blocked_desktop" in section
    assert "in_desktop" in section
    assert "`actor`" in section
    assert "expected_epoch" in section
    assert "X-Barb-Scope" in section
    assert "X-Barb-Lease-Epoch" in section
    assert "X-Barb-Holder" in section
    assert "unchanged" in section
    assert "HTTP 200" in section
    assert "HTTP 503" in section
    text = DOC.read_text(encoding="utf-8")
    assert "not enforced yet" not in text
    assert "DASHBOARD_BARB_FENCE_MODE" in text
    assert "claim_required" in text


def test_inventory_module_records_that_handoff_leaves_surface_unchanged() -> None:
    """Issue #4667 names this module; the docstring must match the UPDATE."""
    docstring = STORE.read_text(encoding="utf-8").split('"""', 2)[1]
    assert "transition_handoff" in docstring
    assert "handoff_state" in docstring
    assert "status" in docstring
    assert "surface" in docstring
    assert "stays as stored" in docstring
    assert "docs/agents/tooling-capabilities-matrix.md" in docstring
