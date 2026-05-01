"""Verify that legacy/App.tsx has no duplicate top-level function declarations.

This catches commented-out dead code and copy-paste drift.
"""

from __future__ import annotations

import re
from pathlib import Path


def test_no_duplicate_top_level_functions_in_legacy() -> None:
    """Every top-level ``function <Name>(...)`` must appear exactly once."""
    src = Path(__file__).parent.parent / "frontend" / "src" / "legacy" / "App.tsx"
    assert src.exists(), f"Source file not found: {src}"

    content = src.read_text(encoding="utf-8")

    # Match line-anchored function declarations (not inside block comments)
    # Use MULTILINE so ^ matches start of each line.
    pattern = re.compile(r"^function (\w+)\b", re.MULTILINE)

    names: list[str] = pattern.findall(content)

    # Build a set of duplicates
    seen: set[str] = set()
    duplicates: set[str] = set()
    for n in names:
        if n in seen:
            duplicates.add(n)
        seen.add(n)

    assert not duplicates, (
        f"Duplicate top-level functions in legacy/App.tsx: {sorted(duplicates)}"
    )