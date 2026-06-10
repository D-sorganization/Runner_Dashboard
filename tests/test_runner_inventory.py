"""Tests for full GitHub Actions runner inventory pagination."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from runner_inventory import fetch_org_runners  # noqa: E402


@pytest.mark.asyncio
async def test_fetch_org_runners_follows_pages_until_total_count() -> None:
    seen: list[str] = []

    async def api(endpoint: str) -> dict:
        seen.append(endpoint)
        if endpoint.endswith("page=1"):
            return {"total_count": 3, "runners": [{"id": 1}, {"id": 2}]}
        return {"total_count": 3, "runners": [{"id": 3}]}

    payload = await fetch_org_runners(api, "D-sorganization")

    assert payload == {"total_count": 3, "runners": [{"id": 1}, {"id": 2}, {"id": 3}]}
    assert seen == [
        "/orgs/D-sorganization/actions/runners?per_page=100&page=1",
        "/orgs/D-sorganization/actions/runners?per_page=100&page=2",
    ]


@pytest.mark.asyncio
async def test_fetch_org_runners_deduplicates_runner_ids() -> None:
    async def api(endpoint: str) -> dict:
        if endpoint.endswith("page=1"):
            return {"total_count": 3, "runners": [{"id": 1}, {"id": 2}]}
        return {"total_count": 3, "runners": [{"id": 2}, {"id": 3}]}

    payload = await fetch_org_runners(api, "D-sorganization")

    assert payload["runners"] == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert payload["total_count"] == 3


@pytest.mark.asyncio
async def test_fetch_org_runners_handles_missing_total_count() -> None:
    seen: list[str] = []

    async def api(endpoint: str) -> dict:
        seen.append(endpoint)
        return {"runners": [{"id": 1}, {"id": 2}]}

    payload = await fetch_org_runners(api, "D-sorganization")

    assert payload["runners"] == [{"id": 1}, {"id": 2}]
    assert payload["total_count"] == 2
    assert seen == ["/orgs/D-sorganization/actions/runners?per_page=100&page=1"]
