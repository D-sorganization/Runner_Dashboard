"""Repo inventory refreshes should reuse per-repository enrichment data."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import Request
from routers import repos


class FakeCache:
    def __init__(self) -> None:
        self.now = 0.0
        self.entries: dict[str, tuple[Any, float]] = {}

    def get(self, key: str, ttl: float) -> Any:
        entry = self.entries.get(key)
        if entry is None:
            return None
        value, created = entry
        return value if self.now - created < ttl else None

    def set(self, key: str, value: Any) -> None:
        self.entries[key] = (value, self.now)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/repos",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("127.0.0.1", 50000),
            "scheme": "http",
        }
    )


@pytest.mark.asyncio
async def test_refresh_reuses_repo_enrichment_and_keeps_response_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = FakeCache()
    repo_rows = [
        {
            "name": name,
            "full_name": f"example/{name}",
            "description": f"description {name}",
            "html_url": f"https://github.com/example/{name}",
            "private": False,
            "language": "Python",
            "default_branch": "main",
            "updated_at": "2026-10-27T00:00:00Z",
            "open_issues_count": 4,
        }
        for name in ("alpha", "beta", "gamma")
    ]
    calls: list[list[str]] = []

    async def run_cmd(argv: list[str], **_kwargs: Any) -> tuple[int, str, str]:
        calls.append(argv)
        endpoint = argv[-1]
        if endpoint.startswith("/orgs/"):
            return 0, json.dumps(repo_rows if "&page=1&" in endpoint else []), ""
        if "/pulls?" in endpoint:
            return 0, "[]", ""
        if "/actions/runs?" in endpoint:
            return 0, json.dumps({"workflow_runs": []}), ""
        raise AssertionError(f"unexpected gh endpoint: {endpoint}")

    monkeypatch.setattr(repos, "_cache_get", cache.get)
    monkeypatch.setattr(repos, "_cache_set", cache.set)
    monkeypatch.setattr(repos, "_run_cmd", run_cmd)
    monkeypatch.setattr(repos, "should_proxy_fleet_to_hub", lambda _request: False)
    monkeypatch.setattr(repos, "ORG", "example")

    cold = await repos.get_repos(_request())
    assert len(calls) == 2 + 2 * len(repo_rows)
    assert set(cold["repos"][0]) == {
        "name",
        "full_name",
        "description",
        "url",
        "private",
        "language",
        "default_branch",
        "updated_at",
        "open_issues_count",
        "open_prs",
        "open_issues",
        "last_ci_status",
        "last_ci_conclusion",
        "last_ci_run_url",
        "last_ci_updated",
    }

    # The complete response expires before each repo's longer enrichment TTL.
    cache.now = repos._REPOS_TTL + 1
    calls.clear()
    refreshed = await repos.get_repos(_request())
    assert len(calls) == 2
    assert all(call[-1].startswith("/orgs/") for call in calls)
    assert refreshed == cold

    calls.clear()
    assert await repos.get_repos(_request()) == cold
    assert calls == []

    cache.now = repos._REPO_ENRICHMENT_TTL + 1
    calls.clear()
    assert await repos.get_repos(_request()) == cold
    assert len(calls) == 2 + 2 * len(repo_rows)
