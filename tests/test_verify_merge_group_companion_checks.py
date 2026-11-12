"""Unit tests for verify_merge_group_companion_checks script (#3450)."""

from __future__ import annotations

from typing import Any

import pytest

from scripts.ci.verify_merge_group_companion_checks import (
    REQUIRED_COMPANION_CHECKS,
    PreconditionError,
    evaluate_checks,
    verify_companion_checks,
)


def test_evaluate_checks_all_successful() -> None:
    check_runs: list[dict[str, Any]] = [
        {"name": "frontend-required", "status": "completed", "conclusion": "success"},
        {"name": "detect-secrets (baseline diff)", "status": "completed", "conclusion": "success"},
        {"name": "api-contract-regeneration", "status": "completed", "conclusion": "success"},
        {"name": "docker-build-scan", "status": "completed", "conclusion": "success"},
        {"name": "unrelated-check", "status": "completed", "conclusion": "failure"},
    ]
    pending, failed = evaluate_checks(check_runs, REQUIRED_COMPANION_CHECKS)
    assert not pending
    assert not failed


def test_evaluate_checks_missing_check_is_pending() -> None:
    check_runs: list[dict[str, Any]] = [
        {"name": "frontend-required", "status": "completed", "conclusion": "success"},
        {"name": "detect-secrets (baseline diff)", "status": "completed", "conclusion": "success"},
        {"name": "api-contract-regeneration", "status": "completed", "conclusion": "success"},
    ]
    pending, failed = evaluate_checks(check_runs, REQUIRED_COMPANION_CHECKS)
    assert pending == ["docker-build-scan (not found yet)"]
    assert not failed


def test_evaluate_checks_in_progress_check_is_pending() -> None:
    check_runs: list[dict[str, Any]] = [
        {"name": "frontend-required", "status": "completed", "conclusion": "success"},
        {"name": "detect-secrets (baseline diff)", "status": "completed", "conclusion": "success"},
        {"name": "api-contract-regeneration", "status": "completed", "conclusion": "success"},
        {"name": "docker-build-scan", "status": "in_progress", "conclusion": None},
    ]
    pending, failed = evaluate_checks(check_runs, REQUIRED_COMPANION_CHECKS)
    assert pending == ["docker-build-scan (status=in_progress)"]
    assert not failed


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "skipped"])
def test_evaluate_checks_non_success_is_failure(conclusion: str) -> None:
    check_runs: list[dict[str, Any]] = [
        {"name": "frontend-required", "status": "completed", "conclusion": "success"},
        {"name": "detect-secrets (baseline diff)", "status": "completed", "conclusion": conclusion},
        {"name": "api-contract-regeneration", "status": "completed", "conclusion": "success"},
        {"name": "docker-build-scan", "status": "completed", "conclusion": "success"},
    ]
    pending, failed = evaluate_checks(check_runs, REQUIRED_COMPANION_CHECKS)
    assert not pending
    assert failed == [f"detect-secrets (baseline diff) (conclusion={conclusion})"]


def test_verify_companion_checks_precondition_errors() -> None:
    with pytest.raises(PreconditionError, match="Commit SHA is required"):
        verify_companion_checks(repo="owner/repo", sha="", token="token")

    with pytest.raises(PreconditionError, match="GitHub token is required"):
        verify_companion_checks(repo="owner/repo", sha="abcdef", token="")
