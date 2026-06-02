"""
CI guard — D5 / issue #706.

Counts rgba( occurrences in frontend/src/index.css and enforces a hard-coded
budget so new status tokens must be introduced via CSS custom properties
(--status-*) rather than inline rgba() literals scattered through component
files.

The budget is a hard-coded constant that represents the approved baseline
established when D5 landed. It does NOT auto-recalculate from the current
file — that would make the test tautological and defeat its purpose.

To lower the budget: refactor rgba() usages to CSS custom properties and
update RGBA_BUDGET to the new count.
To raise the budget: get explicit approval from maintainers and update
RGBA_BUDGET with a comment explaining the exception.
"""

import pathlib
import re

CSS_PATH = pathlib.Path("frontend/src/index.css")

# Hard-coded baseline established at D5 landing (issue #706).
# Lowering this over time is encouraged as literals are migrated
# to semantic custom properties (--status-*, --badge-*, --glass-*).
#
# 2026-06-01 (PR #851, theming polish): raised 73 -> 86. The +13 rgba()
# literals are NOT scattered inline colours — they are the rgba layers of the
# new elevation shadow tokens (--shadow-soft/--shadow-card/--shadow-modal),
# defined once per theme inside :root and consumed everywhere via
# var(--shadow-*). This is exactly the semantic-token pattern this guard
# promotes, so the baseline is raised to cover them. Maintainer-approved.
RGBA_BUDGET = 86


def test_rgba_budget() -> None:
    """Ensure rgba() literal count in index.css does not exceed the approved budget.

    Precondition: CSS_PATH exists and is readable.
    Postcondition: the count of rgba( in the file is at most RGBA_BUDGET.
    """
    assert CSS_PATH.exists(), f"CSS file not found: {CSS_PATH}"
    css = CSS_PATH.read_text(encoding="utf-8")
    count = len(re.findall(r"rgba\(", css))
    assert count <= RGBA_BUDGET, (
        f"Too many hardcoded rgba() literals in {CSS_PATH}: {count} > {RGBA_BUDGET}. "
        "Introduce new colours via CSS custom properties in :root instead, "
        f"or obtain maintainer approval to raise RGBA_BUDGET (currently {RGBA_BUDGET})."
    )
