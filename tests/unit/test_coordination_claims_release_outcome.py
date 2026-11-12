"""Lease-release outcome is classified from evidence, not the agent's say-so (#3274)."""

from __future__ import annotations

from typing import Any

import pytest
from coordination import claims


@pytest.fixture(autouse=True)
def _no_live_github(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claims, "evidence_fetch", lambda _path: pytest.fail("unexpected gh call"))


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
    monkeypatch.setattr(claims, "evidence_fetch", _fetch([], []))
    result = claims.classify_release_reason("Tools", 12, agent_reason="work completed")
    assert result == "abandoned: no PR or branch found for #12; agent reported: work completed"


@pytest.mark.unit
def test_open_pr_closing_the_issue_is_completed_and_linked(monkeypatch: pytest.MonkeyPatch) -> None:
    pr = {"number": 88, "state": "open", "title": "fix: x", "body": "Closes #12", "html_url": "https://x/pr/88"}
    monkeypatch.setattr(claims, "evidence_fetch", _fetch([pr], []))
    result = claims.classify_release_reason("Tools", 12, agent_reason="work completed")
    assert result == "completed: PR #88 https://x/pr/88"


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
    monkeypatch.setattr(claims, "evidence_fetch", _fetch([pr], []))
    result = claims.classify_release_reason("Tools", 12, agent_reason="work completed")
    assert result == "completed: PR #88 https://x/pr/88"


@pytest.mark.unit
def test_closed_unmerged_pr_does_not_count_as_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    pr = {"number": 88, "state": "closed", "merged_at": None, "title": "fix: x", "body": "Closes #12"}
    monkeypatch.setattr(claims, "evidence_fetch", _fetch([pr], []))
    result = claims.classify_release_reason("Tools", 12, agent_reason="work completed")
    assert result.startswith("abandoned:")


@pytest.mark.unit
def test_pushed_branch_with_no_pr_is_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claims, "evidence_fetch", _fetch([], [{"name": "claude/12-fix-release-outcome"}]))
    result = claims.classify_release_reason("Tools", 12, agent_reason="work completed")
    assert result == "completed: branch claude/12-fix-release-outcome pushed"


@pytest.mark.unit
def test_branch_for_a_different_issue_does_not_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claims, "evidence_fetch", _fetch([], [{"name": "claude/120-other-issue"}]))
    result = claims.classify_release_reason("Tools", 12, agent_reason="work completed")
    assert result.startswith("abandoned:")


@pytest.mark.unit
def test_evidence_lookup_unavailable_fails_closed_to_abandoned(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_path: str) -> Any:
        raise RuntimeError("gh api: rate limited")

    monkeypatch.setattr(claims, "evidence_fetch", boom)
    result = claims.classify_release_reason("Tools", 12, agent_reason="work completed")
    assert result.startswith("abandoned: evidence lookup unavailable")
    assert "agent reported: work completed" in result
