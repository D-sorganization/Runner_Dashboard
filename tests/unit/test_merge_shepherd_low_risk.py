"""Unit tests for docs/tests-only low-risk merge shepherd classifier."""

from __future__ import annotations

import pytest

from coordination.merge_shepherd_low_risk import (
    DEFAULT_LOW_RISK_LINE_CAP,
    arm_eligibility_reason,
    classify_docs_tests_only_arm,
    is_docs_or_tests_path,
)

# B-01 reproduced paths (81ec9a70); all must be ineligible for docs/tests-only.
_PATH_INELIGIBLE = (
    "AGENTS.md",
    "CLAUDE.md",
    "skills/cleanup/SKILL.md",
    "tests/conftest.py",
    "docs/../backend/x.py",
)

_PATH_ELIGIBLE = (
    "README.md",
    "docs/agents/guide.md",
    "changes/4245-low-risk.md",
    "tests/unit/test_foo.py",
    "test_merge_shepherd.py",
    "backend/foo_test.py",
)


@pytest.mark.parametrize("path", _PATH_INELIGIBLE)
def test_is_docs_or_tests_path_rejects_review_b01_paths(path: str) -> None:
    assert not is_docs_or_tests_path(path)


@pytest.mark.parametrize("path", _PATH_ELIGIBLE)
def test_is_docs_or_tests_path_accepts_docs_and_tests_only(path: str) -> None:
    assert is_docs_or_tests_path(path)


def test_is_docs_or_tests_path_rejects_product_and_ci_paths() -> None:
    assert not is_docs_or_tests_path("backend/server.py")
    assert not is_docs_or_tests_path(".github/workflows/ci.yml")
    assert not is_docs_or_tests_path("package-lock.json")
    assert not is_docs_or_tests_path("scripts/merge_shepherd_pass.py")
    assert not is_docs_or_tests_path(".cursor/rules/barb.mdc")
    assert not is_docs_or_tests_path("fleet-rules/pr-lifecycle.md")
    assert not is_docs_or_tests_path("pyproject.toml")
    assert not is_docs_or_tests_path("tests/unit/fixtures/sample.json")


def test_classify_rejects_over_line_cap() -> None:
    verdict = classify_docs_tests_only_arm(
        file_paths=["docs/a.md"],
        additions=200,
        deletions=200,
        line_cap=DEFAULT_LOW_RISK_LINE_CAP,
        labels=(),
        checks_state="SUCCESS",
        files_complete=True,
    )
    assert not verdict.eligible
    assert verdict.detail == "over_line_cap"


def test_classify_rejects_mixed_pr() -> None:
    verdict = classify_docs_tests_only_arm(
        file_paths=["docs/a.md", "backend/server.py"],
        additions=10,
        deletions=5,
        line_cap=300,
        labels=(),
        checks_state="SUCCESS",
        files_complete=True,
    )
    assert not verdict.eligible
    assert verdict.detail.startswith("mixed_paths:")


def test_arm_eligibility_reviewed_vs_docs_only() -> None:
    kind, _ = arm_eligibility_reason(
        review_decision="APPROVED",
        file_paths=["backend/x.py"],
        additions=5000,
        deletions=0,
        line_cap=300,
        labels=(),
        checks_state="SUCCESS",
        files_complete=True,
    )
    assert kind == "reviewed"

    kind, detail = arm_eligibility_reason(
        review_decision="REVIEW_REQUIRED",
        file_paths=["docs/agents/x.md", "tests/unit/test_x.py"],
        additions=40,
        deletions=10,
        line_cap=300,
        labels=(),
        checks_state="SUCCESS",
        files_complete=True,
    )
    assert kind == "docs_tests_only"
    assert detail == "docs_tests_only"

    kind, detail = arm_eligibility_reason(
        review_decision=None,
        file_paths=["backend/foo.py"],
        additions=1,
        deletions=0,
        line_cap=300,
        labels=(),
        checks_state="SUCCESS",
        files_complete=True,
    )
    assert kind == "no_approval"
    assert "mixed_paths" in detail
