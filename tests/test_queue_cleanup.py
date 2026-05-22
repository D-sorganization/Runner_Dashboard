"""Tests for backend/queue_cleanup.py — issue #386."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from unittest.mock import patch

import pytest
import queue_cleanup as qc

UTC = getattr(_dt, "UTC", _dt.UTC)


# ---------------------------------------------------------------------------
# StaleRun dataclass
# ---------------------------------------------------------------------------


def test_stale_run_as_dict() -> None:
    run = qc.StaleRun(
        repo="my-repo",
        run_id=12345,
        workflow="CI",
        branch="main",
        created_at="2026-04-01T10:00:00Z",
        age_minutes=90,
    )
    d = run.as_dict()
    assert d["repo"] == "my-repo"
    assert d["run_id"] == 12345
    assert d["age_minutes"] == 90
    assert d["cancelled"] is False


def test_stale_run_cancelled_field() -> None:
    run = qc.StaleRun(
        repo="r",
        run_id=1,
        workflow="w",
        branch="b",
        created_at="2026-04-01T10:00:00Z",
        age_minutes=120,
        cancelled=True,
        cancel_error="",
    )
    assert run.cancelled is True


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


def test_default_min_age_minutes_reasonable() -> None:
    assert qc.DEFAULT_MIN_AGE_MINUTES > 0


def test_scan_concurrency_positive() -> None:
    assert qc._SCAN_CONCURRENCY > 0


def test_cancel_concurrency_positive() -> None:
    assert qc._CANCEL_CONCURRENCY > 0


# ---------------------------------------------------------------------------
# Observability, Metrics, and Audit Trails (Issue #690)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_now():
    fixed_now = _dt.datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)
    with patch("queue_cleanup._get_now", return_value=fixed_now):
        yield fixed_now


def test_audit_trail_operations(tmp_path: Path) -> None:
    temp_audit_file = tmp_path / "test_audit.ndjson"
    with patch("queue_cleanup._AUDIT_FILE", temp_audit_file):
        # Starts empty
        assert qc.load_cleanup_audit() == []
        assert qc.get_last_cleanup_result() is None

        # Append one record
        rec1 = {
            "timestamp": "2026-05-22T12:00:00Z",
            "dry_run": True,
            "stale_count": 2,
            "processed_count": 2,
            "cancelled_count": 0,
            "errors": [],
            "runs": [],
        }
        qc.append_cleanup_audit(rec1)
        assert qc.load_cleanup_audit() == [rec1]
        assert qc.get_last_cleanup_result() == rec1

        # Append another
        rec2 = {
            "timestamp": "2026-05-22T12:01:00Z",
            "dry_run": False,
            "stale_count": 3,
            "processed_count": 3,
            "cancelled_count": 2,
            "errors": ["repo#2: error"],
            "runs": [],
        }
        qc.append_cleanup_audit(rec2)
        # Verify limit and reverse chronological order
        assert qc.load_cleanup_audit() == [rec2, rec1]
        assert qc.load_cleanup_audit(limit=1) == [rec2]
        assert qc.get_last_cleanup_result() == rec2


@pytest.mark.asyncio
async def test_find_stale_runs_metrics(mock_now: _dt.datetime) -> None:
    # Let's mock _gh_json to return some runs
    async def fake_gh_json(*args, **kwargs):
        url = args[0] if len(args) > 0 else ""
        if len(args) > 1:
            url = args[1]
        if "status=queued" in url:
            return {
                "workflow_runs": [
                    {
                        "id": 1,
                        "name": "CI",
                        "head_branch": "agent-1",
                        "head_sha": "sha-1",
                        "event": "push",
                        "status": "queued",
                        "created_at": "2026-05-22T11:00:00Z",  # 60 minutes age
                    }
                ]
            }
        elif "status=in_progress" in url:
            return {"workflow_runs": []}
        return {}

    with (
        patch("queue_cleanup.get_online_runners", return_value=[]),
        patch("queue_cleanup.list_all_repos", return_value=["repo-a"]),
        patch("queue_cleanup._gh_json", side_effect=fake_gh_json),
        patch("prometheus_metrics.record_stale_candidate") as mock_record,
        patch("prometheus_metrics.update_stale_queue_age") as mock_update_age,
        patch("prometheus_metrics.update_stale_queue_age_percentiles") as mock_update_pct,
    ):
        stale_runs = await qc.find_stale_runs("org", min_age_minutes=30)
        assert len(stale_runs) == 1
        assert stale_runs[0].repo == "repo-a"
        assert stale_runs[0].age_minutes == 60

        # Verify that record_stale_candidate is called
        mock_record.assert_called_once_with(reason="abandoned-agent-run")

        # Verify oldest age calculation (60 minutes = 3600 seconds)
        mock_update_age.assert_called_with(3600)
        mock_update_pct.assert_called_once()
        # Verify percentiles dictionary argument
        pct_arg = mock_update_pct.call_args[0][0]
        assert pct_arg["0.5"] == 3600.0


@pytest.mark.asyncio
async def test_purge_stale_runs_and_cancellation_errors(tmp_path: Path, mock_now: _dt.datetime) -> None:
    # Three mock stale candidates
    mock_runs = [
        qc.StaleRun(
            "repo-a",
            1,
            "CI",
            "agent-1",
            "2026-05-22T11:00:00Z",
            60,
            reason="abandoned-agent-run",
            safe_to_cancel=True,
        ),
        qc.StaleRun(
            "repo-b",
            2,
            "CI",
            "feat",
            "2026-05-22T10:00:00Z",
            120,
            reason="stale-feature-branch",
            safe_to_cancel=True,
        ),
        qc.StaleRun(
            "repo-c",
            3,
            "CI",
            "main",
            "2026-05-22T09:00:00Z",
            180,
            reason="stale-main-branch-queue",
            safe_to_cancel=True,
        ),
    ]

    # Mock _gh which is called by _cancel_one:
    # Run 1: succeeds (code=0)
    # Run 2: fails with error (code=1)
    # Run 3: fails with 409 already completed (treated as success)
    async def fake_gh(*args, timeout=30):
        # Join args to search for run cancel urls
        arg_str = " ".join(str(a) for a in args)
        if "/runs/1/cancel" in arg_str:
            return 0, "{}", ""
        elif "/runs/2/cancel" in arg_str:
            return 1, "", "HTTP 401 Unauthorized"
        elif "/runs/3/cancel" in arg_str:
            return 1, "", "Cannot cancel workflow run because it is already finished"
        return 1, "", f"Unknown mock call: {arg_str}"

    temp_audit_file = tmp_path / "test_audit.ndjson"

    with (
        patch("queue_cleanup._AUDIT_FILE", temp_audit_file),
        patch("queue_cleanup.find_stale_runs", return_value=mock_runs),
        patch("queue_cleanup._gh", side_effect=fake_gh),
        patch("prometheus_metrics.record_cancelled_stale_run") as mock_cancelled,
        patch("prometheus_metrics.record_stale_queue_error") as mock_error,
        patch("push.send_push") as mock_send_push,
    ):
        result = await qc.purge_stale_runs("org", min_age_minutes=60, dry_run=False)

        # Verify run outcomes
        assert mock_runs[0].cancelled is True
        assert mock_runs[0].cancel_error == ""

        assert mock_runs[1].cancelled is False
        assert mock_runs[1].cancel_error == "HTTP 401 Unauthorized"

        assert mock_runs[2].cancelled is True
        assert mock_runs[2].cancel_error == "already-finished"

        # Verify that error does not abort the batch. Both successfully completed
        # cancel runs and failed cancel runs are processed.
        # cancelled_count should be 2 (Run 1 and Run 3)
        assert result["cancelled_count"] == 2
        assert result["stale_count"] == 3
        assert len(result["errors"]) == 1
        assert "repo-b#2: HTTP 401 Unauthorized" in result["errors"]

        # Verify metrics calls
        # record_cancelled_stale_run should be called for run 1 and run 3
        assert mock_cancelled.call_count == 2
        # record_stale_queue_error should be called for run 2
        assert mock_error.call_count == 1
        mock_error.assert_called_with(repo="repo-b", reason="stale-feature-branch")

        # Verify push notification was triggered (since there was an error)
        mock_send_push.assert_called_once()
        push_args = mock_send_push.call_args
        assert push_args[0][0] == "queue.stale"
        payload = push_args[0][1]

        # Test push event type and payload size/validity
        assert payload["stale_count"] == 3
        assert payload["errors_count"] == 1

        # Ensure payload is compliant with push module restrictions (under 4KB and no sensitive fields)
        from push import _validate_push_payload

        _validate_push_payload(payload)  # Should not raise exception
