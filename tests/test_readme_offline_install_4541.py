"""README offline-install wording stays honest about the npm cache (issue #4541).

``scripts/verify-lockfile.sh`` runs a plain ``npm ci``. That only works without a
network when the npm cache already holds every package in the lockfile, so the
README must not promise an unconditional offline install. Issue #1085 and its
remainder #1129 are closed, so the README must not cite #1085 as open work.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SECTION_HEADING = "### Deterministic / offline-constrained installs"


def _offline_section() -> str:
    """Return the README offline-install section as one whitespace-normalised string."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert SECTION_HEADING in readme, "the offline-install section was renamed or removed"
    body = readme.split(SECTION_HEADING, 1)[1]
    body = re.split(r"^## ", body, maxsplit=1, flags=re.MULTILINE)[0]
    return " ".join(body.split())


def test_offline_install_claim_is_qualified_by_a_populated_cache() -> None:
    section = _offline_section()
    first_sentence = section.split(". ", 1)[0]
    assert "pre-populated npm cache" in first_sentence
    assert "alone, with no network access, on any fleet machine" not in section


def test_readme_says_an_empty_cache_needs_the_network() -> None:
    assert "empty cache" in _offline_section()


def test_issue_1085_is_not_cited_as_open_work() -> None:
    section = _offline_section()
    assert "is tracked as a larger follow-up in issue #1085" not in section
    assert "#1085" in section and "#1129" in section
    assert "both closed" in section


def test_verify_lockfile_still_runs_a_plain_npm_ci() -> None:
    """The README wording below depends on this; update both together."""
    script = (REPO_ROOT / "scripts" / "verify-lockfile.sh").read_text(encoding="utf-8")
    assert re.search(r"^npm ci$", script, flags=re.MULTILINE)
