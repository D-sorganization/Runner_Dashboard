"""Unit tests for check-run conclusion semantics (TDD, node auto-deploy)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from node_auto_deploy.check_runs import (  # noqa: E402
    check_name_passes,
    completed_run_conclusion_fails,
    normalize_commit_sha,
    run_is_pending,
    validate_check_run_record,
)

_SHA = "a" * 40


def test_normalize_commit_sha_rejects_invalid() -> None:
    assert normalize_commit_sha("short") is None
    assert normalize_commit_sha("g" * 40) is None
    assert normalize_commit_sha(_SHA) == _SHA


def test_validate_check_run_record_requires_name_and_status() -> None:
    assert validate_check_run_record({"name": "ci", "status": "completed"}) is None
    assert validate_check_run_record({}) is not None
    assert validate_check_run_record({"name": "", "status": "completed"}) is not None


def test_run_is_pending_and_conclusions() -> None:
    assert run_is_pending({"status": "in_progress"}) is True
    assert run_is_pending({"status": "queued"}) is True
    assert run_is_pending({"status": "completed"}) is False

    ok = {"status": "completed", "conclusion": "success"}
    assert completed_run_conclusion_fails(ok) is False
    assert completed_run_conclusion_fails({"status": "completed", "conclusion": "skipped"}) is False
    assert completed_run_conclusion_fails({"status": "completed", "conclusion": "neutral"}) is False
    assert completed_run_conclusion_fails({"status": "completed", "conclusion": "failure"}) is True


def test_check_name_passes_aggregates_duplicate_names() -> None:
    assert check_name_passes([{"status": "completed", "conclusion": "skipped", "name": "guard"}]) is True
    both = [
        {"status": "completed", "conclusion": "skipped", "name": "lint"},
        {"status": "completed", "conclusion": "success", "name": "lint"},
    ]
    assert check_name_passes(both) is True
    assert (
        check_name_passes(
            [
                {"status": "completed", "conclusion": "skipped", "name": "lint"},
                {"status": "completed", "conclusion": "failure", "name": "lint"},
            ]
        )
        is False
    )
    assert (
        check_name_passes(
            [
                {"status": "completed", "conclusion": "cancelled", "name": "docker-build-scan"},
                {"status": "completed", "conclusion": "success", "name": "docker-build-scan"},
            ]
        )
        is True
    )
    assert check_name_passes([{"status": "completed", "conclusion": "neutral", "name": "note"}]) is False
