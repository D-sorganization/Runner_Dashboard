"""GET /api/repos enrichment cache (#3175).

Cold loads used two ``gh`` subprocess calls per repository; warm loads must
make zero subprocess calls while preserving the response contract.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import cache_utils
import pytest
from repos_enrichment import (
    build_repos_payload,
    invalidate_repos_enrichment,
    reset_repos_enrichment_cache_for_tests,
)

ORG = "D-sorganization"


def _sample_repo(name: str = "Runner_Dashboard") -> dict[str, Any]:
    full = f"{ORG}/{name}"
    return {
        "name": name,
        "full_name": full,
        "description": "dash",
        "html_url": f"https://example/{name}",
        "private": False,
        "language": "Python",
        "default_branch": "main",
        "updated_at": "2026-10-27T12:00:00Z",
        "open_issues_count": 3,
    }


@pytest.fixture
def gh_env(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record gh argv; default handlers return minimal JSON."""
    calls: list[list[str]] = []
    reset_repos_enrichment_cache_for_tests()
    monkeypatch.setattr(cache_utils, "_main_cache", cache_utils.Cache("repos-enrichment-test"))

    async def fake_run_cmd(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
        calls.append(list(cmd))
        joined = " ".join(cmd)
        if f"/orgs/{ORG}/repos" in joined:
            if "page=2" in joined:
                return 0, "[]", ""
            return 0, json.dumps([_sample_repo()]), ""
        if "pulls?state=open" in joined:
            return 0, json.dumps([{"number": 1}]), ""
        if "actions/runs" in joined:
            return (
                0,
                json.dumps(
                    {
                        "workflow_runs": [
                            {
                                "status": "completed",
                                "conclusion": "success",
                                "html_url": "https://example/run/1",
                                "updated_at": "2026-10-27T11:00:00Z",
                            }
                        ]
                    }
                ),
                "",
            )
        raise AssertionError(f"unexpected gh command: {cmd}")

    monkeypatch.setattr("repos_enrichment._run_gh_off_loop", fake_run_cmd)
    return calls


@pytest.mark.unit
def test_cold_load_fetches_org_and_enrichment(gh_env: list[list[str]]) -> None:
    payload = asyncio.run(build_repos_payload(org=ORG, repos_ttl=120.0))

    assert payload["total_count"] == 1
    repo = payload["repos"][0]
    assert repo["name"] == "Runner_Dashboard"
    assert repo["open_prs"] == 1
    assert repo["last_ci_conclusion"] == "success"
    assert len(gh_env) == 4  # org pages 1–2, pulls, CI runs


@pytest.mark.unit
def test_warm_load_makes_zero_gh_calls(gh_env: list[list[str]]) -> None:
    asyncio.run(build_repos_payload(org=ORG, repos_ttl=120.0))
    gh_env.clear()

    payload = asyncio.run(build_repos_payload(org=ORG, repos_ttl=120.0))

    assert gh_env == []
    assert payload["repos"][0]["open_prs"] == 1


@pytest.mark.unit
def test_invalidate_forces_refresh(gh_env: list[list[str]]) -> None:
    asyncio.run(build_repos_payload(org=ORG, repos_ttl=120.0))
    invalidate_repos_enrichment()
    gh_env.clear()

    asyncio.run(build_repos_payload(org=ORG, repos_ttl=120.0))

    assert len(gh_env) == 4  # org pages 1–2, pulls, CI runs


@pytest.mark.unit
def test_refresh_failure_serves_stale_with_freshness_timestamp(gh_env: list[list[str]]) -> None:
    first = asyncio.run(build_repos_payload(org=ORG, repos_ttl=120.0))
    stale_at = first.get("enriched_at")
    assert stale_at

    async def failing_run_cmd(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
        gh_env.append(list(cmd))
        if f"/orgs/{ORG}/repos" in " ".join(cmd):
            return 0, json.dumps([_sample_repo()]), ""
        return -1, "", "boom"

    cache_utils.cache_delete("repos")
    asyncio.run(asyncio.sleep(0.02))
    gh_env.clear()
    payload = asyncio.run(
        build_repos_payload(org=ORG, repos_ttl=0.01, run_cmd=failing_run_cmd),
    )

    assert gh_env  # attempted refresh
    assert payload["data_source"] == "stale"
    assert payload["enriched_at"] == stale_at
    assert payload["repos"][0]["open_prs"] == 1


@pytest.mark.unit
def test_org_inventory_cached_separately_from_enrichment(gh_env: list[list[str]]) -> None:
    asyncio.run(build_repos_payload(org=ORG, repos_ttl=120.0))
    cache_utils.cache_delete("repos")
    gh_env.clear()

    asyncio.run(build_repos_payload(org=ORG, repos_ttl=120.0))

    # Org inventory and per-repo enrichment remain warm.
    assert gh_env == []


@pytest.mark.unit
def test_enrichment_uses_thread_offload(monkeypatch: pytest.MonkeyPatch) -> None:
    import repos_enrichment as re_mod

    thread_ran = False

    async def capturing_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal thread_ran
        thread_ran = True
        assert func is re_mod._gh_subprocess_sync
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", capturing_to_thread)

    def fake_gh(cmd: list[str], timeout: int) -> tuple[int, str, str]:
        joined = " ".join(cmd)
        if f"/orgs/{ORG}/repos" in joined:
            return 0, json.dumps([_sample_repo()]), ""
        if "pulls" in joined:
            return 0, "[]", ""
        if "actions/runs" in joined:
            return 0, json.dumps({"workflow_runs": []}), ""
        raise AssertionError(cmd)

    monkeypatch.setattr(re_mod, "_gh_subprocess_sync", fake_gh)

    asyncio.run(build_repos_payload(org=ORG, repos_ttl=120.0))
    assert thread_ran
