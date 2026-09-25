"""Tests for Staff Console UX Design Specification invariants (SC-D1, Issue #1301).

Verifies that:
1. `docs/design/staff-console.md` exists and complies with the <= 500 lines constraint.
2. Covers the four-area information architecture (Staff, Work, Fleet, Settings).
3. Enumerates the 6 mandatory design principles.
4. Includes responsive wireframes (Desktop, Tablet, Mobile) for all required views.
5. Specifies roster groupings (Leadership, Project Managers, Specialists, Operations).
6. Defines copy guidelines and standard action verbs.
7. Documents the comprehensive state catalogue.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGN_SPEC = REPO_ROOT / "docs" / "design" / "staff-console.md"


def _read_spec() -> str:
    assert DESIGN_SPEC.is_file(), f"Expected design spec does not exist at {DESIGN_SPEC}"
    return DESIGN_SPEC.read_text(encoding="utf-8")


def test_design_spec_file_exists() -> None:
    """The Staff Console UX spec exists in docs/design/staff-console.md."""
    assert DESIGN_SPEC.is_file(), "docs/design/staff-console.md must exist"


def test_design_spec_line_cap() -> None:
    """Design spec must strictly obey repository <= 500 lines rule."""
    text = _read_spec()
    lines = text.splitlines()
    assert len(lines) <= 500, f"docs/design/staff-console.md has {len(lines)} lines; max allowed is 500"


def test_four_area_information_architecture() -> None:
    """Spec defines the four navigation areas with Staff as default landing page."""
    text = _read_spec()
    lower = text.lower()

    # Core areas
    assert "four-area" in lower or "four areas" in lower
    assert "### 1. staff" in lower or "## 1. staff" in lower or "area: staff" in lower or "staff (default" in lower
    assert "work" in lower
    assert "fleet" in lower
    assert "settings" in lower

    # Default route
    assert "default route" in lower or "landing page" in lower
    assert "/" in text


def test_core_design_principles() -> None:
    """Spec establishes the 6 binding design principles."""
    text = _read_spec()
    lower = text.lower()

    assert "one obvious primary action" in lower or "primary action" in lower
    assert "status honesty" in lower
    assert "progressive disclosure" in lower
    assert "one way to request work" in lower or "ask a role" in lower
    assert "keyboard-first" in lower or "keyboard first" in lower
    assert "mobile parity" in lower


def test_wireframes_coverage() -> None:
    """Spec provides wireframes covering desktop, tablet, and mobile views."""
    text = _read_spec()
    lower = text.lower()

    # Breakpoints
    assert "desktop" in lower
    assert "tablet" in lower
    assert "mobile" in lower

    # Required wireframed views and components
    assert "roster" in lower
    assert "direct thread" in lower or "direct conversation" in lower
    assert "auto (barb)" in lower or "ask barb" in lower
    assert "group thread" in lower or "board" in lower
    assert "action approval" in lower or "action card" in lower
    assert "run card" in lower
    assert "error card" in lower
    assert "empty state" in lower
    assert "first-run" in lower or "first run" in lower


def test_roster_groupings() -> None:
    """Spec defines the 4 roster groups and member assignments."""
    text = _read_spec()
    lower = text.lower()

    assert "leadership" in lower
    assert "project managers" in lower or "project management" in lower
    assert "specialists" in lower
    assert "operations" in lower

    # Role mentions
    assert "barb" in lower
    assert "board" in lower
    assert "maintenance" in lower
    assert "librarian" in lower


def test_copy_guidelines() -> None:
    """Spec provides copy guidelines and standardized action verbs."""
    text = _read_spec()
    lower = text.lower()

    assert "copy guidelines" in lower or "voice and tone" in lower
    assert "plain language" in lower

    # Action verbs
    assert "approve" in lower
    assert "deny" in lower
    assert "cancel" in lower


def test_state_catalogue() -> None:
    """Spec includes complete failure and lifecycle state catalogue."""
    text = _read_spec()
    lower = text.lower()

    assert "loading" in lower
    assert "empty" in lower
    assert "partial failure" in lower or "degraded" in lower
    assert "offline" in lower or "reconnecting" in lower
    assert "provider down" in lower
    assert "node offline" in lower
    assert "permission denied" in lower or "403" in text
