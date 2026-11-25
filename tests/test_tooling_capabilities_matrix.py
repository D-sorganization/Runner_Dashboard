"""The tooling matrix must match the handoff store, not collapse its columns (#4667)."""

from __future__ import annotations

from pathlib import Path

MATRIX = Path(__file__).resolve().parents[1] / "docs" / "agents" / "tooling-capabilities-matrix.md"


def _between(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    stop = text.index(end, begin + len(start))
    return text[begin:stop]


def test_handoff_section_keeps_state_status_surface_and_fences_apart() -> None:
    handoff = _between(
        MATRIX.read_text(encoding="utf-8"),
        "## 2. CLI <-> Desktop App Collaborative Handoff Protocol",
        "## 3. Specialist Role:",
    )
    assert "status = needs_desktop" not in handoff
    assert "status = desktop_claimed" not in handoff
    assert "status = resume_ready" not in handoff
    assert "back to `headless_cli`" not in handoff
    assert "handoff_state=needs_desktop" in handoff
    assert "status=blocked_desktop" in handoff
    assert "handoff_state=desktop_claimed" in handoff
    assert "status=in_desktop" in handoff
    assert "handoff_state=resume_ready" in handoff
    assert "status=in_progress" in handoff
    assert "handoff_state=none" in handoff
    assert "surface" in handoff and "unchanged" in handoff
    assert "`actor`" in handoff
    assert "`expected_epoch`" in handoff
    assert "X-Barb-Scope" in handoff
    assert "X-Barb-Lease-Epoch" in handoff


def test_matrix_does_not_claim_the_lease_fence_is_unenforced() -> None:
    text = MATRIX.read_text(encoding="utf-8")
    assert "not enforced yet" not in text
    assert "DASHBOARD_BARB_FENCE_MODE" in text
    assert "claim_required" in text
