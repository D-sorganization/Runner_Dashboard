"""Tests for per-repo swarm overload detection (issue #3293, slice 1 of 4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import coordination.overload as overload_mod
import gh_client
import pr_inventory
import pytest
from coordination.overload import CONSECUTIVE_WINDOWS_THRESHOLD, DEFAULT_OPEN_COUNT_THRESHOLD, build_overload_snapshot
from delivery_metrics.adapters.github import GhClientDeliveryCollector
from delivery_metrics.models import PrSnapshot

REPO = "D-sorganization/Runner_Dashboard"


def _pr(*, number: int, created_at: datetime, merged_at: datetime | None = None, state: str = "open") -> PrSnapshot:
    return PrSnapshot(
        repo_full=REPO,
        number=number,
        title=f"pr {number}",
        state=state,
        merged=merged_at is not None,
        draft=False,
        head_sha="deadbeef",
        base_ref="main",
        author_login="someone",
        created_at=created_at,
        updated_at=merged_at or created_at,
        closed_at=merged_at,
        merged_at=merged_at,
        additions=1,
        deletions=1,
        changed_files=1,
        html_url=f"https://github.com/{REPO}/pull/{number}",
    )


async def _empty_paginate(_path: str):
    return
    yield  # pragma: no cover - makes this an async generator


def _fake_paginate_n(count: int):
    async def _gen(_path: str):
        for i in range(count):
            yield {"id": i}

    return _gen


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    import cache_utils

    monkeypatch.setattr(cache_utils, "_main_cache", cache_utils.Cache("overload-unit"))


@pytest.fixture(autouse=True)
def _default_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """Healthy, empty-by-default stand-ins for every GitHub-backed source."""

    async def _fake_fetch_repo_prs(_repo: str) -> tuple[list[dict], str | None]:
        return [], None

    async def _fake_list_pulls_updated_since(self, _repo, _since, *, max_pages: int = 3) -> tuple[PrSnapshot, ...]:
        return ()

    async def _fake_get(_path: str) -> Any:
        return []

    monkeypatch.setattr(pr_inventory, "fetch_repo_prs", _fake_fetch_repo_prs)
    monkeypatch.setattr(GhClientDeliveryCollector, "list_pulls_updated_since", _fake_list_pulls_updated_since)
    monkeypatch.setattr(gh_client, "get", _fake_get)
    monkeypatch.setattr(gh_client, "paginate", _empty_paginate)


@pytest.mark.asyncio
async def test_build_overload_snapshot_combines_every_source(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch_repo_prs(_repo: str) -> tuple[list[dict], str | None]:
        return [{"number": 1}, {"number": 2}], None

    monkeypatch.setattr(pr_inventory, "fetch_repo_prs", _fake_fetch_repo_prs)
    monkeypatch.setattr(gh_client, "paginate", _fake_paginate_n(4))

    async def _fake_get(path: str) -> Any:
        if path.endswith("/rulesets"):
            return [{"id": 7, "enforcement": "active"}]
        return {
            "rules": [
                {
                    "type": "merge_queue",
                    "parameters": {
                        "max_entries_to_build": 5,
                        "max_entries_to_merge": 5,
                        "min_entries_to_merge": 1,
                        "min_entries_to_merge_wait_minutes": 5,
                        "check_response_timeout_minutes": 60,
                    },
                }
            ]
        }

    monkeypatch.setattr(gh_client, "get", _fake_get)

    snapshot = await build_overload_snapshot(REPO)

    assert snapshot["repo"] == REPO
    assert snapshot["open_pr_count"] == 2
    assert snapshot["merge_queue_depth"] == 4
    assert snapshot["merge_queue_settings"] == {
        "max_entries_to_build": 5,
        "max_entries_to_merge": 5,
        "min_entries_to_merge": 1,
        "min_entries_to_merge_wait_minutes": 5,
        "check_response_timeout_minutes": 60,
    }
    assert "1h" in snapshot["windows"]
    assert "6h" in snapshot["windows"]
    assert snapshot["overloaded"] is False
    assert snapshot["overload_reasons"] == []
    assert snapshot["warnings"] == []


@pytest.mark.asyncio
async def test_overloaded_by_open_count_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch_repo_prs(_repo: str) -> tuple[list[dict], str | None]:
        return [{"number": i} for i in range(20)], None

    monkeypatch.setattr(pr_inventory, "fetch_repo_prs", _fake_fetch_repo_prs)

    snapshot = await build_overload_snapshot(REPO, open_count_threshold=15)

    assert snapshot["open_pr_count"] == 20
    assert snapshot["overloaded"] is True
    assert any("exceeds threshold 15" in reason for reason in snapshot["overload_reasons"])


@pytest.mark.asyncio
async def test_overloaded_by_consecutive_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    prs: list[PrSnapshot] = []
    number = 0
    for bucket in range(CONSECUTIVE_WINDOWS_THRESHOLD):  # 2 opened/0 merged per trailing 1h bucket
        for _ in range(2):
            number += 1
            created = now - timedelta(hours=bucket, minutes=30)
            prs.append(_pr(number=number, created_at=created))

    async def _fake_list_pulls_updated_since(self, _repo, _since, *, max_pages: int = 3) -> tuple[PrSnapshot, ...]:
        return tuple(prs)

    monkeypatch.setattr(GhClientDeliveryCollector, "list_pulls_updated_since", _fake_list_pulls_updated_since)

    snapshot = await build_overload_snapshot(REPO, open_count_threshold=1000)

    assert snapshot["overloaded"] is True
    assert any("consecutive" in reason for reason in snapshot["overload_reasons"])


@pytest.mark.asyncio
async def test_windows_count_opened_and_merged_events() -> None:
    now = datetime.now(UTC)
    prs = (
        _pr(number=1, created_at=now - timedelta(minutes=10)),
        _pr(number=2, created_at=now - timedelta(hours=3), merged_at=now - timedelta(minutes=5)),
        _pr(number=3, created_at=now - timedelta(hours=10)),  # outside any window
    )
    windows = overload_mod._windows(prs, now=now)

    assert windows["1h"]["open_count"] == 1
    assert windows["1h"]["merge_count"] == 1
    assert windows["6h"]["open_count"] == 2
    assert windows["6h"]["merge_count"] == 1


@pytest.mark.asyncio
async def test_degrades_independently_when_a_source_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(_repo: str) -> tuple[list[dict], str | None]:
        raise RuntimeError("github down")

    monkeypatch.setattr(pr_inventory, "fetch_repo_prs", _boom)

    snapshot = await build_overload_snapshot(REPO)

    assert snapshot["open_pr_count"] == 0
    assert any("open PR count unavailable" in w for w in snapshot["warnings"])
    # the rest of the snapshot is still assembled
    assert "windows" in snapshot
    assert snapshot["merge_queue_depth"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("repo", "open_count_threshold"), [("not-a-slug", DEFAULT_OPEN_COUNT_THRESHOLD), (REPO, 0)])
async def test_rejects_bad_contract_input(repo: str, open_count_threshold: int) -> None:
    with pytest.raises(ValueError):
        await build_overload_snapshot(repo, open_count_threshold=open_count_threshold)


@pytest.mark.asyncio
async def test_no_active_merge_queue_ruleset_reported_as_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_get(path: str) -> Any:
        if path.endswith("/rulesets"):
            return [{"id": 7, "enforcement": "active"}]
        return {"rules": [{"type": "creation"}]}

    monkeypatch.setattr(gh_client, "get", _fake_get)

    snapshot = await build_overload_snapshot(REPO)

    assert snapshot["merge_queue_settings"] is None
    assert any("no active ruleset defines a merge_queue rule" in w for w in snapshot["warnings"])


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    from fastapi.testclient import TestClient
    from identity import require_fleet_peer
    from server import app  # noqa: PLC0415

    async def _fake_build_overload_snapshot(
        repo: str, *, open_count_threshold: int = DEFAULT_OPEN_COUNT_THRESHOLD
    ) -> dict:
        return {
            "repo": repo,
            "open_pr_count": 3,
            "windows": {},
            "merge_queue_depth": 0,
            "merge_queue_settings": None,
            "overloaded": False,
            "overload_reasons": [],
            "open_count_threshold": open_count_threshold,
            "consecutive_windows_threshold": CONSECUTIVE_WINDOWS_THRESHOLD,
            "warnings": [],
        }

    monkeypatch.setattr("routers.overload.build_overload_snapshot", _fake_build_overload_snapshot)
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    try:
        yield TestClient(app, raise_server_exceptions=False, headers={"X-Requested-With": "XMLHttpRequest"})
    finally:
        app.dependency_overrides.pop(require_fleet_peer, None)


def test_get_fleet_overload_returns_snapshot(client: Any) -> None:
    resp = client.get("/api/v1/fleet/overload", params={"repo": REPO})
    assert resp.status_code == 200
    body = resp.json()
    assert body["repo"] == REPO
    assert body["open_pr_count"] == 3


@pytest.mark.parametrize("params", [{}, {"repo": "not-a-slug"}])
def test_get_fleet_overload_rejects_bad_input(client: Any, params: dict[str, str]) -> None:
    resp = client.get("/api/v1/fleet/overload", params=params)
    assert resp.status_code == 422
