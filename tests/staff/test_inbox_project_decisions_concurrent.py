"""The inbox fetches every repo's project overview concurrently (#1712 follow-up).

Awaiting 41 repos one after another made a cold ``GET /api/v1/staff/inbox`` take
17-26 s on DeskComputer, and the Staff page polls it every 30 s.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from projects import service as proj_service
from staff import inbox


@pytest.mark.unit
def test_project_overviews_are_fetched_concurrently_and_keep_repo_order(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = ["A", "B", "C", "D"]
    live = {"now": 0, "peak": 0}

    async def overview(repo: str) -> dict[str, Any]:
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        await asyncio.sleep(0.01)
        live["now"] -= 1
        return {"decisions_needed": [f"decide {repo}"], "generated_at": "2026-09-27T00:00:00Z"}

    monkeypatch.setattr(proj_service, "configured_repos", lambda: repos)
    monkeypatch.setattr(proj_service, "project_overview", overview)

    items = asyncio.run(inbox._collect_project_decisions())  # noqa: SLF001

    assert live["peak"] == len(repos)
    assert [i.metadata["repo"] for i in items] == repos
