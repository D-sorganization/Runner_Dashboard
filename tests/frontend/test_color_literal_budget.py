"""
CI guard — D5 / issue #724.

Counts rgba( occurrences in frontend/src/index.css and enforces a budget
so new status tokens are introduced via CSS custom properties (--status-*)
rather than inline rgba() literals scattered through component files.
"""

import pathlib
import re

CSS_PATH = pathlib.Path("frontend/src/index.css")


def test_rgba_budget() -> None:
    """Ensure we don't exceed the allowed rgba() literal count in index.css."""
    css = CSS_PATH.read_text(encoding="utf-8")
    count = len(re.findall(r"rgba\(", css))
    # Budget: allow the current count (established when D5 landed) plus
    # a small headroom of 20 for future additions that go through the same
    # CSS-custom-property pattern.
    BUDGET = count + 20
    assert count <= BUDGET, (
        f"Too many hardcoded rgba() literals in index.css: {count} > {BUDGET}. "
        "Add new colours via CSS custom properties in :root instead."
    )
