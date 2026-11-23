"""#4627: closing-PR beyond the first open-pulls page must not appear claimable."""

from __future__ import annotations

from typing import Any

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
            return {"total_count": 1, "items": [stale_hit]}
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
