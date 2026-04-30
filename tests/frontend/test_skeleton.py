"""Static checks for the Skeleton primitive (issue #427).

These tests parse the frontend source files (no browser execution) to verify
that the Skeleton primitive exposes the four expected variants, handles
``prefers-reduced-motion: reduce``, and is consumed by at least one of the
target loading-text pages.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKELETON_PATH = REPO_ROOT / "frontend" / "src" / "primitives" / "Skeleton.tsx"
PRIMITIVES_INDEX = REPO_ROOT / "frontend" / "src" / "primitives" / "index.ts"
INDEX_CSS = REPO_ROOT / "frontend" / "src" / "index.css"

TARGET_PAGES = (
    REPO_ROOT / "frontend" / "src" / "pages" / "AgentDispatch.tsx",
    REPO_ROOT / "frontend" / "src" / "pages" / "Fleet" / "Mobile.tsx",
    REPO_ROOT / "frontend" / "src" / "pages" / "LinearSetup.tsx",
)

REQUIRED_VARIANTS = ("Skeleton", "SkeletonLine", "SkeletonCard", "SkeletonTable")


def _read(path: Path) -> str:
    assert path.exists(), f"missing required file: {path}"
    return path.read_text(encoding="utf-8")


def test_skeleton_module_exists() -> None:
    assert SKELETON_PATH.exists(), "Skeleton.tsx should exist"


@pytest.mark.parametrize("variant", REQUIRED_VARIANTS)
def test_skeleton_exports_variant(variant: str) -> None:
    """Each of the four variants must be exported from Skeleton.tsx."""
    src = _read(SKELETON_PATH)
    assert f"export function {variant}" in src, f"Skeleton.tsx must export `{variant}` as a function component"


def test_primitives_barrel_re_exports_all_variants() -> None:
    barrel = _read(PRIMITIVES_INDEX)
    for variant in REQUIRED_VARIANTS:
        assert variant in barrel, f"primitives/index.ts must re-export `{variant}`"


def test_skeleton_handles_reduced_motion() -> None:
    """Skeleton must reference prefers-reduced-motion (directly or via CSS)."""
    src = _read(SKELETON_PATH)
    css = _read(INDEX_CSS)
    combined = src + "\n" + css
    assert "prefers-reduced-motion" in combined, (
        "Skeleton primitive must reference `prefers-reduced-motion` so reduced "
        "motion is respected (in Skeleton.tsx or the global stylesheet)."
    )
    assert "prefers-reduced-motion" in src, (
        "Skeleton.tsx must mention prefers-reduced-motion in source comments "
        "or code so the reduced-motion contract is discoverable from the "
        "primitive itself."
    )


def test_at_least_one_page_imports_skeleton() -> None:
    """At least one of the three target pages imports from primitives/Skeleton."""
    needles = (
        'from "../primitives/Skeleton"',
        'from "../../primitives/Skeleton"',
        "from '../primitives/Skeleton'",
        "from '../../primitives/Skeleton'",
    )
    matches: list[Path] = []
    for page in TARGET_PAGES:
        if not page.exists():
            continue
        body = _read(page)
        if any(needle in body for needle in needles):
            matches.append(page)
    assert matches, (
        "At least one of the target pages "
        f"({', '.join(p.name for p in TARGET_PAGES)}) must import from "
        "`primitives/Skeleton`."
    )


def test_skeleton_file_under_500_lines() -> None:
    """Repo policy: source files stay under 500 lines."""
    lines = SKELETON_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 500, f"Skeleton.tsx must be <= 500 lines, found {len(lines)}"


def test_skeleton_has_no_todo_or_fixme() -> None:
    src = _read(SKELETON_PATH)
    assert "TODO" not in src, "Skeleton.tsx must not contain TODO markers"
    assert "FIXME" not in src, "Skeleton.tsx must not contain FIXME markers"
