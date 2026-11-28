"""Lease-release outcome is classified from evidence, not the agent's say-so (#3274)."""

from __future__ import annotations

from typing import Any

import pytest
from coordination import claims
from tests.api.coordination_fake_rm import patch_gh_list_fetch


@pytest.fixture(autouse=True)
def _no_live_github(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_path: str) -> Any:
        pytest.fail("unexpected gh call")

    patch_gh_list_fetch(monkeypatch, boom)


def _fetch(pulls: list[dict[str, Any]], branches: list[dict[str, Any]]) -> claims.EvidenceFetch:
    def fetch(path: str) -> Any:
        if "/pulls" in path:
            return pulls
        if "/branches" in path:
            return branches
        raise AssertionError(f"unexpected gh api path: {path}")

    return fetch


@pytest.mark.unit
def test_no_pr_and_no_branch_is_abandoned_with_agents_reason_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_gh_list_fetch(monkeypatch, _fetch([], []))
    result = claims.classify_release_reason("Tools", 12, agent_reason="work completed")
    assert result == "abandoned: no PR or branch found for #12; agent reported: work completed"


@pytest.mark.unit
def test_open_pr_closing_the_issue_is_pr_open_and_linked(monkeypatch: pytest.MonkeyPatch) -> None:
    pr = {"number": 88, "state": "open", "title": "fix: x", "body": "Closes #12", "html_url": "https://x/pr/88"}
    patch_gh_list_fetch(monkeypatch, _fetch([pr], []))
    result = claims.classify_release_reason("Tools", 12, agent_reason="work completed")
    assert result == "pr_open: PR #88 https://x/pr/88"


@pytest.mark.unit
def test_merged_pr_closing_the_issue_is_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    pr = {
        "number": 88,
        "state": "closed",
        "merged_at": "2026-10-28T00:00:00Z",
        "title": "fix: x",
        "body": "Fixes #12",
        "html_url": "https://x/pr/88",
    }
    patch_gh_list_fetch(monkeypatch, _fetch([pr], []))
    result = claims.classify_release_reason("Tools", 12, agent_reason="work completed")
    assert result == "completed: PR #88 https://x/pr/88"


@pytest.mark.unit
def test_closed_unmerged_pr_does_not_count_as_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    pr = {"number": 88, "state": "closed", "merged_at": None, "title": "fix: x", "body": "Closes #12"}
    patch_gh_list_fetch(monkeypatch, _fetch([pr], []))
    result = claims.classify_release_reason("Tools", 12, agent_reason="work completed")
    assert result.startswith("abandoned:")


@pytest.mark.unit
def test_pushed_branch_with_no_pr_is_branch_pushed(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_gh_list_fetch(monkeypatch, _fetch([], [{"name": "claude/12-fix-release-outcome"}]))
    result = claims.classify_release_reason("Tools", 12, agent_reason="work completed")
    assert result == "branch_pushed: branch claude/12-fix-release-outcome pushed"


@pytest.mark.unit
def test_branch_for_a_different_issue_does_not_count(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_gh_list_fetch(monkeypatch, _fetch([], [{"name": "claude/120-other-issue"}]))
    result = claims.classify_release_reason("Tools", 12, agent_reason="work completed")
    assert result.startswith("abandoned:")


@pytest.mark.unit
def test_evidence_lookup_unavailable_fails_closed_to_abandoned(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_path: str) -> Any:
        raise RuntimeError("gh api: rate limited")

    patch_gh_list_fetch(monkeypatch, boom)
    result = claims.classify_release_reason("Tools", 12, agent_reason="work completed")
    assert result.startswith("abandoned: evidence lookup unavailable")
    assert "agent reported: work completed" in result


@pytest.mark.unit
def test_pulls_lookup_is_one_bounded_recency_sorted_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """#4546: ``--paginate .../pulls?state=all`` walks the whole PR history and blows the 20s hook budget."""
    from coordination import gh_cli

    seen: list[str] = []

    def fetch(path: str) -> Any:
        seen.append(path)
        return []

    def no_paginate(path: str) -> Any:
        if "/pulls" in path:
            pytest.fail(f"pulls lookup must not use the unbounded paginator: {path}")
        return []

    patch_gh_list_fetch(monkeypatch, fetch)
    monkeypatch.setattr(gh_cli, "gh_api_json_all", no_paginate)
    claims.classify_release_reason("Tools", 12, agent_reason="work completed")
    pulls_paths = [p for p in seen if "/pulls" in p]
    assert len(pulls_paths) == 1
    assert "state=all" in pulls_paths[0]
    assert "sort=updated" in pulls_paths[0]
    assert "direction=desc" in pulls_paths[0]
    assert "per_page=100" in pulls_paths[0]
