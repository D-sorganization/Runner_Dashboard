"""Unit tests for docs/tests-only low-risk merge shepherd classifier."""

from __future__ import annotations

from coordination.merge_shepherd_low_risk import (
    DEFAULT_LOW_RISK_LINE_CAP,
    arm_eligibility_reason,
    classify_docs_tests_only_arm,
    is_docs_or_tests_path,
)


def test_is_docs_or_tests_path_patterns():
    assert is_docs_or_tests_path("README.md")
    assert is_docs_or_tests_path("docs/agents/guide.md")
    assert is_docs_or_tests_path("changes/4245-low-risk.md")
    assert is_docs_or_tests_path("tests/unit/test_foo.py")
    assert is_docs_or_tests_path("test_merge_shepherd.py")
    assert is_docs_or_tests_path("backend/foo_test.py")
    assert not is_docs_or_tests_path("backend/server.py")
    assert not is_docs_or_tests_path(".github/workflows/ci.yml")
    assert not is_docs_or_tests_path("package-lock.json")
    assert not is_docs_or_tests_path("scripts/merge_shepherd_pass.py")


def test_classify_rejects_over_line_cap():
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


def test_classify_rejects_mixed_pr():
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


def test_arm_eligibility_reviewed_vs_docs_only():
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
