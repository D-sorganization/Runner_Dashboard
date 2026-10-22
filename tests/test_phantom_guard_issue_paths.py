"""Regression tests for anti-phantom Rule 3 path extraction (#2123)."""

from __future__ import annotations

from pathlib import Path

from scripts.phantom_guard_issue_paths import issue_body_repo_paths

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "issues" / "2123-weekly-summary-body.md"
_VERIFY_ISSUE_ACTION = (
    Path(__file__).resolve().parents[1] / ".github" / "actions" / "verify-issue-resolution" / "action.yml"
)


def test_issue_2123_body_does_not_yield_http_route_as_repo_path() -> None:
    body = _FIXTURE.read_text(encoding="utf-8")
    assert issue_body_repo_paths(body) == []


def test_verify_issue_resolution_action_uses_shared_extractor() -> None:
    text = _VERIFY_ISSUE_ACTION.read_text(encoding="utf-8")
    assert "phantom_guard_issue_paths.py" in text
    assert "grep -oE '(backend|frontend/src|src|tests|rust_core|api)/" not in text


def test_backend_paths_are_still_detected() -> None:
    body = "Touch `backend/delivery_metrics/weekly_summary.py` and tests/unit/test_x.py."
    assert issue_body_repo_paths(body) == [
        "backend/delivery_metrics/weekly_summary.py",
        "tests/unit/test_x.py",
    ]
