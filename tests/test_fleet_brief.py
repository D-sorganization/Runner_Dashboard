"""Tests for the cached fleet board (issue #3314)."""

from __future__ import annotations

from typing import Any

import cache_utils
import coordination.brief as brief_mod
import pytest
import routers.fleet as fleet_mod
import routers.queue as queue_mod
import routers.repos as repos_mod


async def _async_result(payload: dict[str, Any], warnings: list[str]) -> tuple[dict[str, Any], list[str]]:
    return payload, warnings


class _FakeInventory:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def fetch_all_prs(self, repos: list[str], **kwargs: Any) -> dict[str, Any]:
        return self._payload

    async def fetch_all_issues(self, repos: list[str], **kwargs: Any) -> dict[str, Any]:
        return self._payload


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_utils, "_main_cache", cache_utils.Cache("fleet-brief-unit"))
    monkeypatch.setattr(cache_utils, "_swr_refreshes", {})


@pytest.fixture(autouse=True)
def _fake_briefing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_build_briefing(repo: str | None, agent: str | None) -> dict[str, Any]:
        return {
            "holds": [{"text": "no deploy"}],
            "staff_runs": [{"status": "running"}],
            "priorities": [],
            "warnings": [],
        }

    monkeypatch.setattr(brief_mod, "build_briefing", _fake_build_briefing)


@pytest.mark.asyncio
async def test_build_fleet_brief_combines_every_source(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_queue_impl() -> dict[str, Any]:
        return {"queued_count": 2, "in_progress_count": 1}

    async def _fake_collect(*, exclude_pools: bool) -> dict[str, Any]:
        return {"hub": {"status": "online"}}

    monkeypatch.setattr(queue_mod, "_queue_impl", _fake_queue_impl)
    monkeypatch.setattr(fleet_mod, "_collect_fleet_status", _fake_collect)
    monkeypatch.setattr(repos_mod, "_pr_inventory", _FakeInventory({"items": [1, 2]}))
    monkeypatch.setattr(repos_mod, "_issue_inventory", _FakeInventory({"items": [1]}))
    monkeypatch.setattr(brief_mod, "_queue_shepherd", lambda repo: _async_result({"repo": repo}, []))

    brief = await brief_mod.build_fleet_brief("D-sorganization/Runner_Dashboard")

    assert brief["repo"] == "D-sorganization/Runner_Dashboard"
    assert brief["queue"] == {"queued_count": 2, "in_progress_count": 1}
    assert brief["hub_status"] == {"hub": {"status": "online"}}
    assert brief["prs"] == {"items": [1, 2]}
    assert brief["issues"] == {"items": [1]}
    assert brief["holds"] == [{"text": "no deploy"}]
    assert brief["queue_shepherd"] == {"repo": "D-sorganization/Runner_Dashboard"}
    assert brief["warnings"] == []


@pytest.mark.asyncio
async def test_build_fleet_brief_degrades_independently_on_a_failing_source(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom() -> dict[str, Any]:
        raise RuntimeError("queue down")

    async def _fake_collect(*, exclude_pools: bool) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(queue_mod, "_queue_impl", _boom)
    monkeypatch.setattr(fleet_mod, "_collect_fleet_status", _fake_collect)
    monkeypatch.setattr(repos_mod, "_pr_inventory", _FakeInventory({"items": []}))
    monkeypatch.setattr(repos_mod, "_issue_inventory", _FakeInventory({"items": []}))
    monkeypatch.setattr(brief_mod, "_queue_shepherd", lambda repo: _async_result({}, []))

    brief = await brief_mod.build_fleet_brief("D-sorganization/Runner_Dashboard")

    assert brief["queue"] == {}
    assert any("queue unavailable" in w for w in brief["warnings"])
    assert brief["prs"] == {"items": []}  # the rest of the brief still assembled


@pytest.mark.asyncio
async def test_queue_shepherd_section_reports_depth_and_recommendations(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared_scripts.queue_shepherd_logic import DependencyViolation
    from shared_scripts.queue_shepherd_poll import QueueShepherdReport

    def _fake_poll_queue(owner: str, name: str) -> QueueShepherdReport:
        assert (owner, name) == ("D-sorganization", "Runner_Dashboard")
        return QueueShepherdReport(
            repo=f"{owner}/{name}",
            queue_depth=2,
            dependency_violations=(DependencyViolation(2780, 1, 2775, 2),),
        )

    monkeypatch.setattr("shared_scripts.queue_shepherd_poll.poll_queue", _fake_poll_queue)

    section, warnings = await brief_mod._queue_shepherd("D-sorganization/Runner_Dashboard")

    assert warnings == []
    assert section["queue_depth"] == 2
    assert section["dependency_violations"] == [
        {"pr": 2780, "pr_position": 1, "blocked_by": 2775, "blocked_by_position": 2}
    ]
    assert "recommend moving #2775" in section["brief"]


@pytest.mark.asyncio
async def test_queue_shepherd_section_degrades_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(owner: str, name: str) -> Any:
        raise RuntimeError("gh unavailable")

    monkeypatch.setattr("shared_scripts.queue_shepherd_poll.poll_queue", _boom)

    section, warnings = await brief_mod._queue_shepherd("D-sorganization/Runner_Dashboard")

    assert section == {}
    assert any("queue-shepherd unavailable" in w for w in warnings)


@pytest.mark.asyncio
async def test_queue_shepherd_section_skips_when_no_repo_given() -> None:
    section, warnings = await brief_mod._queue_shepherd(None)

    assert section == {} and warnings == []


def test_render_markdown_reports_counts_and_warnings() -> None:
    brief = {
        "repo": "D-sorganization/Runner_Dashboard",
        "generated_at": "2026-10-28T00:00:00Z",
        "issues": {"items": [1, 2]},
        "prs": {"items": [1]},
        "queue": {"queued_count": 3, "in_progress_count": 0},
        "holds": [{"text": "no deploy"}],
        "staff_runs": [{"status": "running"}, {"status": "succeeded"}],
        "hub_status": {"node-a": {"status": "offline"}},
        "queue_shepherd": {"queue_depth": 4, "dependency_violations": [1], "path_conflicts": []},
        "warnings": ["queue unavailable: boom"],
    }

    md = brief_mod.render_markdown(brief)

    assert "Open issues: 2" in md
    assert "Open PRs: 1" in md
    assert "3 queued, 0 in progress" in md
    assert "Active holds: 1" in md
    assert "no deploy" in md
    assert "Active staff runs: 1" in md  # only the running one counts
    assert "Hub nodes offline: 1 (node-a)" in md
    assert "Queue-shepherd: depth=4, 1 dependency issue(s), 0 conflict(s)" in md
    assert "queue unavailable: boom" in md


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    from fastapi.testclient import TestClient
    from identity import require_fleet_peer
    from server import app  # noqa: PLC0415

    async def _fake_build_fleet_brief(repo: str | None, *, limit: int = 100) -> dict[str, Any]:
        return {"repo": repo, "generated_at": "2026-10-28T00:00:00Z", "issues": {}, "prs": {}, "warnings": []}

    monkeypatch.setattr("routers.fleet_brief.build_fleet_brief", _fake_build_fleet_brief)
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    try:
        yield TestClient(app, raise_server_exceptions=False, headers={"X-Requested-With": "XMLHttpRequest"})
    finally:
        app.dependency_overrides.pop(require_fleet_peer, None)


def test_get_fleet_brief_returns_json_with_an_etag(client: Any) -> None:
    resp = client.get("/api/v1/fleet/brief")
    assert resp.status_code == 200
    assert resp.json()["repo"] is None
    assert resp.headers["ETag"]


def test_get_fleet_brief_returns_304_when_etag_matches(client: Any) -> None:
    first = client.get("/api/v1/fleet/brief")
    etag = first.headers["ETag"]
    second = client.get("/api/v1/fleet/brief", headers={"If-None-Match": etag})
    assert second.status_code == 304


def test_get_fleet_brief_markdown_format(client: Any) -> None:
    resp = client.get("/api/v1/fleet/brief", params={"format": "markdown"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "Fleet brief" in resp.text
