"""The vendored handoff rules must match Repository_Management's validator (CR-4, #1285).

Runs only when a Repository_Management checkout sits beside this repository (local
development hosts); CI skips it because the dashboard never depends on a sibling repo.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from code_requests import handoff_rules  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANDIDATES = [
    parent / "Repository_Management" / "shared_scripts" / "handoff_validator.py"
    for parent in (_REPO_ROOT.parent, _REPO_ROOT.parent.parent)
]


def _load_source() -> ModuleType:
    path = next((p for p in _CANDIDATES if p.is_file()), None)
    if path is None:
        pytest.skip("no Repository_Management checkout beside this repository")
    spec = importlib.util.spec_from_file_location("rm_handoff_validator", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_vendored_rules_match_the_source() -> None:
    source = _load_source()
    assert [h for h, _ in handoff_rules.REQUIRED_HEADINGS] == [h for h, _ in source.REQUIRED_HEADINGS]
    assert [p.pattern for _, p in handoff_rules.REQUIRED_HEADINGS] == [p.pattern for _, p in source.REQUIRED_HEADINGS]
    assert handoff_rules.REQUIRED_IDENTITY_FIELDS == source.REQUIRED_IDENTITY_FIELDS
    assert handoff_rules.PLACEHOLDER_PATTERN.pattern == source.PLACEHOLDER_PATTERN.pattern
    assert handoff_rules.HEX_COMMIT_PATTERN.pattern == source.HEX_COMMIT_PATTERN.pattern
    assert [p.pattern for p, _ in handoff_rules.SECRET_PATTERNS] == [p.pattern for p, _ in source.SECRET_PATTERNS]


@pytest.mark.parametrize(
    "doc",
    [
        "# Implementation Handoff\n\n## Identity\n\n- Repository: x\n",
        "no title at all",
        "# Implementation Handoff\n\n## Next steps\n\n1. run <command>\n",
    ],
)
def test_vendored_rules_agree_with_the_source_on_sample_documents(doc: str) -> None:
    source = _load_source()
    theirs = source.validate_handoff_content(doc, Path("HANDOFF.md"))
    assert len(handoff_rules.handoff_findings(doc)) == len(theirs)
