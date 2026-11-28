"""RD#4710 (FC-007): classify_release_reason must distinguish merged PR (completed),
open PR (pr_open), and branch-only (branch_pushed) rather than mapping all to completed."""

from __future__ import annotations

from typing import Any

import pytest
from coordination import claims
from tests.api.coordination_fake_rm import patch_gh_list_fetch


def _fetch(pulls: list[dict[str, Any]], branches: list[dict[str, Any]]) -> claims.EvidenceFetch:
    def fetch(path: str) -> Any:
        if "/pulls" in path:
            return pulls
        if "/branches" in path:
            return branches
        raise AssertionError(f"unexpected gh api path: {path}")

    return fetch


@pytest.mark.unit
def test_merged_pr_classify_as_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    pr = {
        "number": 42,
        "state": "closed",
        "merged_at": "2026-11-01T00:00:00Z",
        "title": "fix: release outcome",
        "body": "Fixes #100",
        "html_url": "https://github.com/org/repo/pull/42",
    }
    patch_gh_list_fetch(monkeypatch, _fetch([pr], []))
    result = claims.classify_release_reason("Tools", 100, agent_reason="done")
    assert result == "completed: PR #42 https://github.com/org/repo/pull/42"


@pytest.mark.unit
def test_open_pr_classify_as_pr_open(monkeypatch: pytest.MonkeyPatch) -> None:
    pr = {
        "number": 43,
        "state": "open",
        "merged_at": None,
        "title": "fix: release outcome",
        "body": "Closes #101",
        "html_url": "https://github.com/org/repo/pull/43",
    }
    patch_gh_list_fetch(monkeypatch, _fetch([pr], []))
    result = claims.classify_release_reason("Tools", 101, agent_reason="done")
    assert result == "pr_open: PR #43 https://github.com/org/repo/pull/43"


@pytest.mark.unit
def test_branch_only_classify_as_branch_pushed(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_gh_list_fetch(monkeypatch, _fetch([], [{"name": "claude-desk-2/102-fix-thing"}]))
    result = claims.classify_release_reason("Tools", 102, agent_reason="done")
    assert result == "branch_pushed: branch claude-desk-2/102-fix-thing pushed"


@pytest.mark.unit
def test_open_pr_missing_merged_at_key_classify_as_pr_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """PR dict with no merged_at key at all (not just None) must still be pr_open."""
    pr = {
        "number": 44,
        "state": "open",
        "title": "fix: x",
        "body": "Closes #103",
        "html_url": "https://github.com/org/repo/pull/44",
    }
    patch_gh_list_fetch(monkeypatch, _fetch([pr], []))
    result = claims.classify_release_reason("Tools", 103, agent_reason="done")
    assert result == "pr_open: PR #44 https://github.com/org/repo/pull/44"
