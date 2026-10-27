"""GET /api/repos enrichment cache (#3175)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import cache_utils
import pytest
from repos_enrichment import build_repos_payload, invalidate_repos_enrichment, reset_repos_enrichment_cache_for_tests

ORG = "D-sorganization"
_RUN = {"status": "completed", "conclusion": "success", "html_url": "https://x/r", "updated_at": "2026-10-27T11:00:00Z"}


@pytest.fixture
def repos_cache(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []
    reset_repos_enrichment_cache_for_tests()
    monkeypatch.setattr(cache_utils, "_main_cache", cache_utils.Cache("repos-enrichment-test"))

    async def fake(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
        calls.append(list(cmd))
        s = " ".join(cmd)
        if f"/orgs/{ORG}/repos" in s:
            return (0, "[]", "") if "page=2" in s else (
                0,
                json.dumps(
                    [
                        {
                            "name": "Runner_Dashboard",
                            "full_name": f"{ORG}/Runner_Dashboard",
                            "open_issues_count": 3,
                        }
                    ]
                ),
                "",
            )
        if "pulls" in s:
            return 0, json.dumps([{"number": 1}]), ""
        if "actions/runs" in s:
            return 0, json.dumps({"workflow_runs": [_RUN]}), ""
        raise AssertionError(cmd)

    monkeypatch.setattr("repos_enrichment._run_gh_off_loop", fake)

    def run(**kw: Any) -> dict[str, Any]:
        return asyncio.run(build_repos_payload(org=ORG, repos_ttl=120.0, **kw))

    return calls, run


@pytest.mark.unit
def test_cold_warm_and_layered_cache(repos_cache) -> None:
    calls, run = repos_cache
    p = run()
    assert p["repos"][0]["open_prs"] == 1 and len(calls) == 4
    calls.clear()
    assert run()["repos"][0]["open_prs"] == 1 and calls == []

    calls.clear()
    cache_utils.cache_delete("repos")
    run()
    assert calls == []

    invalidate_repos_enrichment()
    calls.clear()
    run()
    assert len(calls) == 4


@pytest.mark.unit
def test_stale_on_refresh_failure(repos_cache) -> None:
    calls, run = repos_cache
    stale_at = run()["enriched_at"]
    cache_utils.cache_delete("repos")

    async def fail(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
        calls.append(list(cmd))
        return (
            (0, json.dumps([{"name": "Runner_Dashboard", "full_name": f"{ORG}/Runner_Dashboard", "open_issues_count": 3}]), "")
            if f"/orgs/{ORG}/repos" in " ".join(cmd)
            else (-1, "", "x")
        )

    calls.clear()
    asyncio.run(asyncio.sleep(0.02))
    p = asyncio.run(build_repos_payload(org=ORG, repos_ttl=0.01, run_cmd=fail))
    assert calls and p["data_source"] == "stale" and p["enriched_at"] == stale_at
    assert p["repos"][0]["open_prs"] == 1


@pytest.mark.unit
def test_gh_subprocess_runs_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import repos_enrichment as mod

    reset_repos_enrichment_cache_for_tests()
    seen = False

    async def capture(func: Any, *a: Any, **k: Any) -> Any:
        nonlocal seen
        seen = func is mod._gh_subprocess_sync
        return func(*a, **k)

    monkeypatch.setattr(asyncio, "to_thread", capture)
    monkeypatch.setattr(
        mod,
        "_gh_subprocess_sync",
        lambda cmd, timeout: (0, json.dumps([{"name": "r", "full_name": f"{ORG}/r", "open_issues_count": 0}]), "")
        if "/orgs/" in " ".join(cmd)
        else (0, json.dumps({"workflow_runs": []}), ""),
    )
    asyncio.run(build_repos_payload(org=ORG, repos_ttl=120.0))
    assert seen
