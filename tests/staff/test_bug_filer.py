"""Tests for RD #2236: Deduplicated, rate-limited failure -> bug issue filer."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from staff.bug_filer import (
    BugFiler,
    compute_failure_fingerprint,
    handle_fleet_event,
    handle_terminal_failure,
    normalize_error_message,
    set_bug_filer,
)


@pytest.fixture
def mock_gh() -> MagicMock:
    client = MagicMock()
    client.create_issue.return_value = {
        "number": 101,
        "html_url": "https://github.com/D-sorganization/Runner_Dashboard/issues/101",
    }
    client.create_comment.return_value = {
        "id": 202,
        "html_url": "https://github.com/D-sorganization/Runner_Dashboard/issues/101#202",
    }
    return client


@pytest.fixture
def filer(tmp_path: Path, mock_gh: MagicMock, monkeypatch: pytest.MonkeyPatch) -> BugFiler:
    monkeypatch.setenv("RD_BUG_FILER", "on")
    return BugFiler(db_path=tmp_path / "bug_filer.sqlite", gh_client=mock_gh)


def test_fingerprint_normalization() -> None:
    e1 = "Process failed in /home/runner/work/RD/run-1/test.py: error 0x7f at 2026-10-23T01:00:00Z"
    e2 = "Process failed in C:\\Users\\runner\\work\\RD\\run-2\\test.py: error 0x12 at 2026-10-23T02:00:00Z"
    assert normalize_error_message(e1) == normalize_error_message(e2)
    assert compute_failure_fingerprint("config", "r", "repo", e1) == compute_failure_fingerprint(
        "config", "r", "repo", e2
    )


def test_dry_run_makes_zero_github_calls(filer: BugFiler, mock_gh: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RD_BUG_FILER", "dry_run")
    decision = filer.process_failure("workspace_error", "coder", "Runner_Dashboard", "err", "last", "run-1")
    assert decision["action"] == "would_file" and decision["dry_run"] is True
    assert mock_gh.create_issue.call_count == 0 and mock_gh.create_comment.call_count == 0


def test_dedup_one_issue_plus_daily_comment(filer: BugFiler, mock_gh: MagicMock) -> None:
    # First occurrence files issue
    d1 = filer.process_failure("env_err", "coder", "RD", "bwrap error", "last", "run-1")
    assert d1["action"] == "filed" and mock_gh.create_issue.call_count == 1

    # Same failure within 24h -> deduped
    d2 = filer.process_failure("env_err", "coder", "RD", "bwrap error", "last", "run-2")
    assert d2["action"] == "deduped" and mock_gh.create_issue.call_count == 1 and mock_gh.create_comment.call_count == 0

    # Repeat occurrence after 24h adds comment
    past = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    with sqlite3.connect(filer.db_path) as conn:
        conn.execute("UPDATE bug_filer_state SET last_commented_at=?", (past,))

    d3 = filer.process_failure("env_err", "coder", "RD", "bwrap error", "last", "run-3")
    assert d3["action"] == "commented" and mock_gh.create_comment.call_count == 1


def test_retryable_failure_below_threshold_not_filed(
    tmp_path: Path, mock_gh: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RD_BUG_FILER", "on")
    f = BugFiler(db_path=tmp_path / "bug_filer.sqlite", gh_client=mock_gh, retryable_threshold=3)
    assert f.process_failure("rate_limited", "p", "RD", "429", "last", "r1")["action"] == "pending_threshold"
    assert f.process_failure("rate_limited", "p", "RD", "429", "last", "r2")["action"] == "pending_threshold"
    assert f.process_failure("rate_limited", "p", "RD", "429", "last", "r3")["action"] == "filed"
    assert mock_gh.create_issue.call_count == 1


def test_daily_cap_and_digest(tmp_path: Path, mock_gh: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RD_BUG_FILER", "on")
    f = BugFiler(db_path=tmp_path / "bug_filer.sqlite", gh_client=mock_gh, max_issues_per_day=5)
    for i in range(5):
        assert f.process_failure("config", f"r{i}", "RD", f"err {i}", "last", f"run-{i}")["action"] == "filed"
    assert mock_gh.create_issue.call_count == 5
    assert f.process_failure("config", "r6", "RD", "err 6", "last", "run-6")["action"] == "daily_cap_digest"
    assert mock_gh.create_issue.call_count == 5


def test_redaction_in_issue_body(filer: BugFiler, mock_gh: MagicMock) -> None:
    secret = "ghp_" + "TestTokenForBugFilerRedaction1234567"  # pragma: allowlist secret
    filer.process_failure("workspace_error", "coder", "RD", f"Auth {secret}", f"Token {secret}", "run-sec")
    call_args = mock_gh.create_issue.call_args[1]
    assert secret not in call_args["body"] and secret not in call_args["title"]
    assert "[REDACTED]" in call_args["body"]


def test_github_api_failure_does_not_raise(filer: BugFiler, mock_gh: MagicMock) -> None:
    mock_gh.create_issue.side_effect = RuntimeError("GitHub API 500")
    assert filer.process_failure("timeout", "coder", "RD", "timed out", "timeout", "run-err")["action"] == "error"


def test_handle_terminal_failure_and_fleet_event(filer: BugFiler) -> None:
    set_bug_filer(filer)
    assert handle_terminal_failure(MagicMock(status="completed"))["action"] == "ignored"

    failed_run = MagicMock(
        status="failed", failure_class="config", role="c", repo="RD", error="err", last_line="l", id="r1"
    )
    assert handle_terminal_failure(failed_run)["action"] == "filed"

    assert handle_fleet_event(MagicMock(severity="info"))["action"] == "ignored"
    crit = MagicMock(severity="critical", kind="low_disk", title="Low", detail="0.5G", node="n1")
    assert handle_fleet_event(crit)["action"] == "filed"
