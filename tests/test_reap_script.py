from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts/ directory is in sys.path so we can import from reap_queued_jobs
_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import reap_queued_jobs  # noqa: E402


def test_str_to_bool() -> None:
    assert reap_queued_jobs.str_to_bool("true") is True
    assert reap_queued_jobs.str_to_bool("True") is True
    assert reap_queued_jobs.str_to_bool("1") is True
    assert reap_queued_jobs.str_to_bool(True) is True
    assert reap_queued_jobs.str_to_bool("false") is False
    assert reap_queued_jobs.str_to_bool("False") is False
    assert reap_queued_jobs.str_to_bool("0") is False
    assert reap_queued_jobs.str_to_bool(False) is False


@patch("reap_queued_jobs.urllib.request.urlopen")
def test_reap_script_disabled(mock_urlopen: MagicMock) -> None:
    with patch.dict(os.environ, {"QUEUED_JOB_REAPER_DISABLED": "true"}):
        with pytest.raises(SystemExit) as excinfo:
            reap_queued_jobs.main()
        assert excinfo.value.code == 0
    mock_urlopen.assert_not_called()


@patch("reap_queued_jobs.urllib.request.urlopen")
def test_reap_script_success(mock_urlopen: MagicMock, tmp_path: Path) -> None:
    # Set up dummy GITHUB_STEP_SUMMARY file
    summary_file = tmp_path / "summary.md"

    # Mock data for GET
    mock_get_response = MagicMock()
    mock_get_response.__enter__.return_value = mock_get_response
    mock_get_response.read.return_value = json.dumps(
        {
            "stale_count": 2,
            "runs": [
                {
                    "repo": "repo1",
                    "run_id": 101,
                    "workflow": "CI",
                    "branch": "feat-1",
                    "reason": "superseded_pr_head",
                    "safe_to_cancel": True,
                    "url": "http://github.com/org/repo1/actions/runs/101",
                },
                {
                    "repo": "repo2",
                    "run_id": 102,
                    "workflow": "Deploy",
                    "branch": "main",
                    "reason": "stale-main-branch-queue",
                    "safe_to_cancel": False,
                    "url": "http://github.com/org/repo2/actions/runs/102",
                },
            ],
        }
    ).encode("utf-8")

    # Mock data for POST
    mock_post_response = MagicMock()
    mock_post_response.__enter__.return_value = mock_post_response
    mock_post_response.read.return_value = json.dumps(
        {
            "timestamp": "2026-05-22T00:00:00Z",
            "dry_run": True,
            "stale_count": 2,
            "processed_count": 2,
            "cancelled_count": 0,
            "errors": [],
            "runs": [
                {
                    "repo": "repo1",
                    "run_id": 101,
                    "workflow": "CI",
                    "branch": "feat-1",
                    "reason": "superseded_pr_head",
                    "safe_to_cancel": True,
                    "url": "http://github.com/org/repo1/actions/runs/101",
                },
                {
                    "repo": "repo2",
                    "run_id": 102,
                    "workflow": "Deploy",
                    "branch": "main",
                    "reason": "stale-main-branch-queue",
                    "safe_to_cancel": False,
                    "url": "http://github.com/org/repo2/actions/runs/102",
                },
            ],
        }
    ).encode("utf-8")

    mock_urlopen.side_effect = [mock_get_response, mock_post_response]

    test_args = [
        "reap_queued_jobs.py",
        "--dry-run",
        "true",
        "--min-age-minutes",
        "45",
        "--safe-to-cancel-only",
        "true",
    ]

    with patch.object(sys, "argv", test_args):
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_file), "QUEUED_JOB_REAPER_DISABLED": "false"}):
            reap_queued_jobs.main()

    # Verify requests were made
    assert mock_urlopen.call_count == 2
    get_req = mock_urlopen.call_args_list[0][0][0]
    assert "min_age_minutes=45" in get_req.full_url
    assert "safe_to_cancel_only=true" in get_req.full_url

    post_req = mock_urlopen.call_args_list[1][0][0]
    assert post_req.method == "POST"
    body_data = json.loads(post_req.data.decode("utf-8"))
    assert body_data["min_age_minutes"] == 45
    assert body_data["dry_run"] is True
    assert body_data["safe_to_cancel_only"] is True

    # Verify GITHUB_STEP_SUMMARY contents
    assert summary_file.exists()
    content = summary_file.read_text(encoding="utf-8")
    assert "## Queued Job Reaper Summary" in content
    assert "- **Dry Run:** `True`" in content
    assert "### Counts by Reason" in content
    assert "| `superseded_pr_head` | 1 |" in content
    assert "| `stale-main-branch-queue` | 1 |" in content
