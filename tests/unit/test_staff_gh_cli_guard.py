"""Unit tests for pre-close issue evidence guard and PR body sanitizer (#2408)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from staff.gh_cli_guard import check_and_prepare_issue_close, sanitize_pr_body_args

pytestmark = pytest.mark.unit


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["gh"], returncode=returncode, stdout=stdout, stderr="")


def test_sanitize_pr_body_unescapes_literal_newlines_and_appends_target_issue() -> None:
    raw = ["pr", "create", "--title", "fix(staff): test", "--body", "Summary line.\\n\\nCloses #2474.\\n\\nTest plan."]
    out = sanitize_pr_body_args(raw, env={"STAFF_TARGET_KIND": "issue", "STAFF_TARGET_REF": "2474"})
    body = out[out.index("--body") + 1]
    assert "\\n" not in body
    assert "\nCloses #2474" in body

    raw_missing = ["pr", "create", "--title", "fix: missing", "--body", "Implemented fix."]
    out_missing = sanitize_pr_body_args(raw_missing, env={"STAFF_TARGET_KIND": "issue", "STAFF_TARGET_REF": "2454"})
    assert "Closes #2454" in out_missing[out_missing.index("--body") + 1]


def test_issue_close_blocked_without_evidence_or_exempt_label() -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[1:3] == ["issue", "view"]:
            return _completed(stdout=json.dumps({"labels": [{"name": "bug"}], "comments": []}))
        if cmd[1:3] == ["pr", "list"]:
            return _completed(stdout=json.dumps([]))
        raise AssertionError(f"Unexpected command: {cmd}")

    with patch("staff.gh_cli_guard.subprocess.run", side_effect=fake_run):
        allowed, _, err = check_and_prepare_issue_close("/usr/bin/gh", ["issue", "close", "2408"], env={})
    assert not allowed
    assert "2408" in err and "Verify-Issue-Closure" in err


def test_issue_close_allowed_when_comment_cites_merged_pr_and_posts_comment_first() -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[1:3] == ["pr", "view"]:
            return _completed(
                stdout=json.dumps({"number": 2456, "mergedAt": "2026-10-23T12:00Z", "body": "Summary.", "labels": []})
            )
        if cmd[1:4] == ["api", "-X", "PATCH"] or cmd[1:3] == ["issue", "comment"]:
            return _completed()
        raise AssertionError(f"Unexpected command: {cmd}")

    with patch("staff.gh_cli_guard.subprocess.run", side_effect=fake_run):
        allowed, new_args, err = check_and_prepare_issue_close(
            "/usr/bin/gh", ["issue", "close", "2454", "--comment", "Verified in PR #2456."], env={}
        )
    assert allowed and err == "" and new_args == ["issue", "close", "2454"]
    assert any(c[1:3] == ["issue", "comment"] for c in calls)
    assert any(c[1:4] == ["api", "-X", "PATCH"] for c in calls)


def test_issue_close_allowed_with_exempt_label() -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return _completed(stdout=json.dumps({"labels": [{"name": "duplicate"}], "comments": []}))

    with patch("staff.gh_cli_guard.subprocess.run", side_effect=fake_run):
        allowed, _, err = check_and_prepare_issue_close("/usr/bin/gh", ["issue", "close", "2408"], env={})
    assert allowed and err == ""


def test_verify_issue_closure_workflow_has_comment_pr_check_and_newline_normalization() -> None:
    wf_path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "Verify-Issue-Closure.yml"
    script = yaml.safe_load(wf_path.read_text(encoding="utf-8"))["jobs"]["verify-closure"]["steps"][0]["with"]["script"]
    for token in ("replace(/\\\\n/g, '\\n')", "pulls.get", "verified-closed", "MAX_REOPENS"):
        assert token in script
