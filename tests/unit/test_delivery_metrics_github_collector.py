"""GhClientDeliveryCollector behaviour (#2118)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import gh_client
import pytest
from delivery_metrics.adapters.github import GhClientDeliveryCollector
from gh_client import GhRateLimited

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "delivery_metrics"
REPO = "D-sorganization/Runner_Dashboard"


@pytest.fixture
def collector() -> GhClientDeliveryCollector:
    return GhClientDeliveryCollector(concurrency=2)


@pytest.mark.asyncio
async def test_fetch_pull_uses_gh_client_and_cache(
    monkeypatch: pytest.MonkeyPatch, collector: GhClientDeliveryCollector
) -> None:
    payload = json.loads((FIXTURES / "pr_2111.json").read_text(encoding="utf-8"))
    calls: list[str] = []

    async def fake_get(path: str) -> Any:
        calls.append(path)
        return payload

    monkeypatch.setattr(gh_client, "get", fake_get)
    monkeypatch.setattr(gh_client, "get_auth_fingerprint", lambda: "test-fp")

    first = await collector.fetch_pull(REPO, 2111)
    second = await collector.fetch_pull(REPO, 2111)
    assert first.number == 2111
    assert second.head_sha == first.head_sha
    assert calls == [f"/repos/{REPO}/pulls/2111"]


@pytest.mark.asyncio
async def test_list_check_runs_from_fixture(
    monkeypatch: pytest.MonkeyPatch, collector: GhClientDeliveryCollector
) -> None:
    payload = json.loads((FIXTURES / "check_runs_pr_2111.json").read_text(encoding="utf-8"))
    head_sha = payload["check_runs"][0]["head_sha"]

    async def fake_get(path: str) -> Any:
        assert head_sha in path
        return payload

    monkeypatch.setattr(gh_client, "get", fake_get)
    monkeypatch.setattr(gh_client, "get_auth_fingerprint", lambda: "test-fp")

    runs = await collector.list_check_runs(REPO, head_sha)
    assert len(runs) >= 20
    assert runs[0].head_sha == head_sha


@pytest.mark.asyncio
async def test_list_pulls_updated_since_filters_old(
    monkeypatch: pytest.MonkeyPatch, collector: GhClientDeliveryCollector
) -> None:
    pr_payload = json.loads((FIXTURES / "pr_2111.json").read_text(encoding="utf-8"))
    old = dict(pr_payload)
    old["number"] = 1
    old["updated_at"] = "2020-01-01T00:00:00Z"

    async def fake_get(path: str) -> Any:
        return [pr_payload, old]

    monkeypatch.setattr(gh_client, "get", fake_get)
    monkeypatch.setattr(gh_client, "get_auth_fingerprint", lambda: "test-fp")

    since = datetime(2026, 10, 1, tzinfo=UTC)
    pulls = await collector.list_pulls_updated_since(REPO, since, max_pages=1)
    assert len(pulls) == 1
    assert pulls[0].number == 2111


@pytest.mark.asyncio
async def test_rate_limit_propagates(monkeypatch: pytest.MonkeyPatch, collector: GhClientDeliveryCollector) -> None:
    async def fake_get(path: str) -> Any:
        raise GhRateLimited(retry_after_seconds=60, endpoint=path)

    monkeypatch.setattr(gh_client, "get", fake_get)
    monkeypatch.setattr(gh_client, "get_auth_fingerprint", lambda: "test-fp")

    with pytest.raises(GhRateLimited):
        await collector.fetch_pull(REPO, 1)
