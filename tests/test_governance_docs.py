"""Tests for public-repo governance docs (issue #3182, epic #3077).

Verifies that:
1. `CODE_OF_CONDUCT.md` exists and names a reporting contact, with no
   infrastructure specifics (hosts, tailnet addresses, internal paths).
2. `SECURITY.md` already covers a reporting contact and process (pre-existing,
   unchanged by this issue).
3. Generic bug/feature issue templates for outside contributors exist and
   carry no fleet-internal dispatch metadata.
4. The README has a capability matrix describing standalone vs overlay-backed
   behaviour.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CODE_OF_CONDUCT = REPO_ROOT / "CODE_OF_CONDUCT.md"
SECURITY_MD = REPO_ROOT / "SECURITY.md"
ISSUE_TEMPLATE_DIR = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
BUG_REPORT_YML = ISSUE_TEMPLATE_DIR / "bug_report.yml"
FEATURE_REQUEST_YML = ISSUE_TEMPLATE_DIR / "feature_request.yml"
README_MD = REPO_ROOT / "README.md"

_INFRA_TERMS = ("tailnet", "ssh ", "internal path", "/home/", "[bot]")


def _read(path: Path) -> str:
    assert path.is_file(), f"Expected document does not exist: {path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def test_code_of_conduct_exists_with_contact_and_no_infra_details() -> None:
    text = _read(CODE_OF_CONDUCT)
    assert "security/advisories/new" in text, "CODE_OF_CONDUCT.md must name a reporting contact"
    lowered = text.lower()
    for term in _INFRA_TERMS:
        assert term not in lowered, f"CODE_OF_CONDUCT.md must not mention infra detail {term!r}"


def test_security_md_already_covers_contact_and_process() -> None:
    text = _read(SECURITY_MD)
    assert "Reporting a Vulnerability" in text
    assert "security/advisories/new" in text


def test_generic_issue_templates_exist_for_outside_contributors() -> None:
    for path in (BUG_REPORT_YML, FEATURE_REQUEST_YML):
        data = yaml.safe_load(_read(path))
        assert "name" in data and "body" in data
        serialized = str(data)
        assert "dispatch-metadata" not in serialized
        assert "complexity" not in serialized
        assert "judgement" not in serialized


def test_readme_has_capability_matrix() -> None:
    text = _read(README_MD)
    assert "Capability matrix" in text
    assert "standalone" in text.lower()
    assert "overlay" in text.lower()
