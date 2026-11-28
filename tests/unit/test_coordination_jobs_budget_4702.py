"""Job-board listing must return inside the coordination response budget (#4702)."""

from __future__ import annotations

import asyncio
import time

from coordination import claims_check_cache
from coordination import jobs as jobs_mod
from routers import coordination


def _issue(number: int) -> dict:
    return {
        "number": number,
        "title": f"issue {number}",
        "labels": [],
        "created_at": f"2026-01-{number:02d}T00:00:00Z",
        "html_url": f"https://example.test/{number}",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def test_list_jobs_keeps_verified_jobs_when_a_later_claim_check_exceeds_budget(monkeypatch) -> None:
    """A slow claim check must not discard jobs already verified or stall past the budget."""
    issues = [_issue(11), _issue(12)]
    monkeypatch.setattr(jobs_mod, "_candidates", lambda *_args, **_kwargs: (issues, True))
    monkeypatch.setattr(jobs_mod, "repo_schedule_holds", lambda _repo: ([], True))
    monkeypatch.setattr(jobs_mod, "_safe_top_priorities", lambda _limit: ([], []))
    monkeypatch.setattr(jobs_mod, "_closing_pr", lambda *_args, **_kwargs: None)

    def check(_repo: str, issue: int) -> dict:
        if issue == 12:
            time.sleep(1.5)
        return {
            "available": True,
            "held": False,
            "agent": "",
            "session": "",
            "reason": "",
            "expires_at": None,
        }

    monkeypatch.setattr(jobs_mod.claims_mod, "check", check)
    claims_check_cache.reset()
    started = time.monotonic()
    result = jobs_mod.list_jobs(
        repo="Runner_Dashboard",
        limit=10,
        include_excluded=True,
        budget_seconds=0.35,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert [job["issue"] for job in result["jobs"]] == [11]
    assert result["complete"] is False
    assert any("budget" in warning.lower() for warning in result["warnings"])
    claims_check_cache.reset()


def test_get_jobs_gives_list_jobs_a_budget_under_the_route_timeout(monkeypatch) -> None:
    seen: dict = {}

    def capture(**kwargs):
        seen.update(kwargs)
        return {
            "available": True,
            "complete": True,
            "jobs": [],
            "warnings": [],
            "generated_at": "2026-01-01T00:00:00Z",
        }

    monkeypatch.setattr(coordination.jobs_mod, "list_jobs", capture)

    async def call() -> None:
        await coordination.get_jobs(repo="Runner_Dashboard", limit=5, include_excluded=False, _peer="test")

    asyncio.run(call())
    assert seen["budget_seconds"] < coordination.COORDINATION_TIMEOUT
    assert seen["budget_seconds"] > 0
