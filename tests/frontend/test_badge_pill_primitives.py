"""Tests for the Badge and Pill UI primitives.

These tests parse the TSX source files for the new primitives introduced
in issue #422 and assert that:

* `frontend/src/primitives/Badge.tsx` exposes the five canonical tones
  (`success`, `warning`, `danger`, `info`, `neutral`) and the two sizes
  (`sm`, `md`).
* `frontend/src/primitives/Pill.tsx` exposes a `selected` prop.
* At least five consumer files import from the new primitives.

The tests intentionally parse source rather than execute the React tree —
the dashboard frontend is exercised via Vite/Vitest in browser CI, while
this Python suite enforces the structural contract that downstream code
relies on.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PRIMITIVES_DIR = REPO_ROOT / "frontend" / "src" / "primitives"
SRC_DIR = REPO_ROOT / "frontend" / "src"


def _read(path: Path) -> str:
    assert path.exists(), f"expected {path} to exist"
    return path.read_text(encoding="utf-8")


def test_badge_primitive_exports_five_tones() -> None:
    """Badge.tsx must declare BadgeTone with all five tones."""
    source = _read(PRIMITIVES_DIR / "Badge.tsx")
    for tone in ("success", "warning", "danger", "info", "neutral"):
        assert f'"{tone}"' in source, f"Badge.tsx missing tone '{tone}'"
    assert "BadgeTone" in source, "Badge.tsx must export BadgeTone type"


def test_badge_primitive_exports_two_sizes() -> None:
    """Badge.tsx must declare BadgeSize with 'sm' and 'md'."""
    source = _read(PRIMITIVES_DIR / "Badge.tsx")
    for size in ("sm", "md"):
        assert f'"{size}"' in source, f"Badge.tsx missing size '{size}'"
    assert "BadgeSize" in source, "Badge.tsx must export BadgeSize type"


def test_badge_primitive_default_tone_is_neutral() -> None:
    """Badge default tone must be 'neutral' (lowest visual weight)."""
    source = _read(PRIMITIVES_DIR / "Badge.tsx")
    assert 'tone = "neutral"' in source, "Badge.tsx must default tone to 'neutral'"
    assert 'size = "md"' in source, "Badge.tsx must default size to 'md'"


def test_pill_primitive_exports_selected_prop() -> None:
    """Pill.tsx must accept a `selected` boolean prop."""
    source = _read(PRIMITIVES_DIR / "Pill.tsx")
    assert "selected?: boolean" in source, "Pill.tsx must declare selected?: boolean"
    assert "selected = false" in source, "Pill.tsx must default selected to false"


def test_pill_primitive_uses_aria_pressed() -> None:
    """Pill.tsx must wire selected -> aria-pressed for accessibility."""
    source = _read(PRIMITIVES_DIR / "Pill.tsx")
    assert "aria-pressed={selected}" in source


def test_primitives_index_re_exports_badge_and_pill() -> None:
    """The primitives barrel must re-export both new primitives."""
    source = _read(PRIMITIVES_DIR / "index.ts")
    assert 'from "./Badge"' in source
    assert 'from "./Pill"' in source
    assert "Badge" in source
    assert "Pill" in source


def test_at_least_five_consumer_files_import_primitives() -> None:
    """Issue #422 requires migrating at least 5 ad-hoc badge usages."""
    consumers: list[Path] = []
    for tsx in SRC_DIR.rglob("*.tsx"):
        # Skip the primitive definitions themselves and their tests.
        if tsx.is_relative_to(PRIMITIVES_DIR):
            continue
        text = tsx.read_text(encoding="utf-8")
        if (
            'from "../../primitives/Badge"' in text
            or 'from "../../primitives/Pill"' in text
            or 'from "../primitives/Badge"' in text
            or 'from "../primitives/Pill"' in text
        ):
            consumers.append(tsx)

    rels = sorted(str(p.relative_to(REPO_ROOT)) for p in consumers)
    assert len(consumers) >= 5, (
        f"expected at least 5 consumer files importing Badge/Pill, found {len(consumers)}: {rels}"
    )


def test_design_tokens_declare_badge_css_variables() -> None:
    """tokens.ts must register all 10 badge CSS variables."""
    source = _read(SRC_DIR / "design" / "tokens.ts")
    for tone in ("success", "warning", "danger", "info", "neutral"):
        assert f'"--badge-{tone}-bg"' in source
        assert f'"--badge-{tone}-fg"' in source


def test_index_css_declares_badge_css_variables() -> None:
    """index.css must mirror the badge CSS variables for runtime use."""
    source = _read(SRC_DIR / "index.css")
    for tone in ("success", "warning", "danger", "info", "neutral"):
        assert f"--badge-{tone}-bg:" in source
        assert f"--badge-{tone}-fg:" in source
