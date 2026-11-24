"""#4622: job-board closing-PR evidence must not paginate ``pulls?state=all`` per fleet repo."""

from __future__ import annotations

from typing import Any

import pytest
from coordination import gh_cli
from coordination import jobs as jobs_mod


@pytest.mark.unit
def test_closing_pr_lookup_is_one_bounded_recency_sorted_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """``list_jobs`` walks every fleet repo; each must use a single recency page, not ``--paginate``."""
    seen: list[str] = []

    def fetch(path: str) -> list[Any]:
        seen.append(path)
        return []

    def no_paginate(path: str) -> list[Any]:
        if "/pulls" in path:
            pytest.fail(f"pulls lookup must not use the unbounded paginator: {path}")
        return []

    monkeypatch.setattr(gh_cli, "gh_api_json", fetch)
    monkeypatch.setattr(gh_cli, "gh_api_json_all", no_paginate)

    cache: dict[str, list[Any]] = {}
    jobs_mod._closing_pr("D-sorganization/Tools", 12, cache)
    jobs_mod._closing_pr("D-sorganization/Tools", 13, cache)

    pulls_paths = [p for p in seen if "/pulls" in p]
    assert len(pulls_paths) == 1
    assert pulls_paths[0] == jobs_mod.bounded_open_pulls_path("D-sorganization/Tools")
