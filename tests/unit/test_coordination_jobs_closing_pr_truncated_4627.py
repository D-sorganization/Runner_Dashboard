"""#4627: closing-PR beyond the first open-pulls page must not appear claimable."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from coordination import gh_cli
from coordination import jobs as jobs_mod

TOOLS = "D-sorganization/Tools"


def _filler_pulls(count: int) -> list[dict[str, Any]]:
    return [{"number": i, "title": f"pr-{i}", "body": "", "state": "open"} for i in range(1, count + 1)]


@pytest.mark.unit
def test_closing_pr_beyond_first_page_uses_targeted_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the bounded page is full, a closing PR outside page 1 is found via search."""
    stale_hit = {
        "number": 999,
        "title": "fix: land feature",
        "body": "Closes #42\n",
        "state": "open",
    }
    search_calls: list[str] = []

    def fetch(path: str) -> Any:
        if path == jobs_mod.bounded_open_pulls_path(TOOLS):
            return _filler_pulls(jobs_mod.CLOSING_PR_PULLS_PER_PAGE)
        if path.startswith("/search/issues?"):
            search_calls.append(path)
            return {"total_count": 1, "items": [stale_hit], "incomplete_results": False}
        return []

    monkeypatch.setattr(gh_cli, "gh_api_json", fetch)
    cache: dict[str, list[Any]] = {}
    truncated: dict[str, bool] = {}

    hit = jobs_mod._closing_pr(TOOLS, 42, cache, truncated_by_repo=truncated)

    assert hit == stale_hit
    assert truncated[TOOLS] is True
    assert len(search_calls) == 1
    assert search_calls[0] == jobs_mod.closing_pr_search_path(TOOLS, 42)


@pytest.mark.unit
def test_closing_pr_search_not_used_when_page_not_full(monkeypatch: pytest.MonkeyPatch) -> None:
    search_calls: list[str] = []

    def fetch(path: str) -> Any:
        if path.startswith("/search/issues?"):
            search_calls.append(path)
        if path == jobs_mod.bounded_open_pulls_path(TOOLS):
            return _filler_pulls(5)
        return []

    monkeypatch.setattr(gh_cli, "gh_api_json", fetch)
    cache: dict[str, list[Any]] = {}
    truncated: dict[str, bool] = {}

    assert jobs_mod._closing_pr(TOOLS, 99, cache, truncated_by_repo=truncated) is None
    assert truncated.get(TOOLS) is False
    assert search_calls == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "search_result",
    [
        {"items": [], "total_count": 0, "incomplete_results": True},
        {
            "items": [{"title": "Refs #42", "body": ""}] * 10,
            "total_count": 11,
            "incomplete_results": False,
        },
        [],
        {},
        {"items": None, "total_count": 0, "incomplete_results": False},
        {"items": [], "total_count": "0", "incomplete_results": False},
        {"items": [], "total_count": 0},
        {"items": [], "total_count": -1, "incomplete_results": False},
        {"items": [None], "total_count": 1, "incomplete_results": False},
    ],
)
def test_jobs_excludes_issue_when_closing_search_is_inconclusive(
    monkeypatch: pytest.MonkeyPatch, search_result: Any
) -> None:
    calls: list[str] = []

    def fetch(path: str) -> Any:
        calls.append(path)
        if path == jobs_mod.bounded_open_pulls_path(TOOLS):
            return _filler_pulls(jobs_mod.CLOSING_PR_PULLS_PER_PAGE)
        assert path.startswith("/search/issues?")
        return search_result

    monkeypatch.setattr(gh_cli, "gh_api_json", fetch)
    monkeypatch.setattr(jobs_mod, "_candidates", lambda *args: ([{"number": 42, "labels": []}], True))
    monkeypatch.setattr(jobs_mod, "repo_schedule_holds", lambda repo: ([], True))
    monkeypatch.setattr(jobs_mod, "_safe_top_priorities", lambda limit: ([], []))
    monkeypatch.setattr(jobs_mod.claims_mod, "check", lambda *args: {"available": True, "held": False})

    result = jobs_mod.list_jobs(repo="Tools", limit=10, include_excluded=True)

    assert result["jobs"] == []
    assert result["complete"] is False
    assert result["excluded"][0]["issue"] == 42
    assert "PR lookup unavailable" in result["excluded"][0]["reason"]
    assert any("PR lookup unavailable" in warning for warning in result["warnings"])
    assert len(calls) == 2  # One recency page and one search; never paginate to resolve uncertainty.


@pytest.mark.unit
def test_title_only_closing_reference_beyond_recency_page(monkeypatch: pytest.MonkeyPatch) -> None:
    hit = {"number": 999, "title": "Closes #42", "body": "", "state": "open"}

    def fetch(path: str) -> Any:
        if path == jobs_mod.bounded_open_pulls_path(TOOLS):
            return _filler_pulls(jobs_mod.CLOSING_PR_PULLS_PER_PAGE)
        query = parse_qs(urlsplit(path).query)["q"][0]
        items = [] if "in:body" in query.split() else [hit]
        return {"items": items, "total_count": len(items), "incomplete_results": False}

    monkeypatch.setattr(gh_cli, "gh_api_json", fetch)
    assert jobs_mod._closing_pr(TOOLS, 42, {}) == hit


@pytest.mark.unit
def test_complete_search_without_closing_references_is_conclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gh_cli,
        "gh_api_json",
        lambda path: {
            "items": [{"title": "Refs #42", "body": ""}],
            "total_count": 1,
            "incomplete_results": False,
        },
    )
    assert jobs_mod._search_open_closing_pr(TOOLS, 42) is None
