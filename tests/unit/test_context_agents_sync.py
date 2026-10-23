from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from fastapi import HTTPException
from staff.agents_sync import build_agents_sync_report

pytestmark = pytest.mark.unit

FetchFile = Callable[[str, str], Awaitable[str | None]]


@pytest.mark.asyncio
async def test_reports_managed_block_drift_against_repository_management_source() -> None:
    canonical = """Intro\n<!-- BEGIN FLEET-MANAGED: policy -->\nPolicy v2\n<!-- END FLEET-MANAGED: policy -->\n"""
    files = {
        ("Repository_Management", "AGENTS.md"): canonical,
        ("UpstreamDrift", "AGENTS.md"): canonical,
        ("AffineDrift", "AGENTS.md"): (
            "Intro\n<!-- BEGIN FLEET-MANAGED: policy -->\nPolicy v1\n<!-- END FLEET-MANAGED: policy -->\n"
        ),
    }

    async def fetch(repo: str, path: str) -> str | None:
        return files.get((repo, path))

    report = await build_agents_sync_report(fetch, ["UpstreamDrift", "AffineDrift"])

    assert report["source"] == "Repository_Management/AGENTS.md"
    assert report["source_ref"] == "GitHub Contents API default branch"
    assert report["checked_at"].endswith("+00:00")
    rows = {row["repo"]: row for row in report["repositories"]}
    assert rows["AffineDrift"]["status"] == "lagging"
    assert rows["AffineDrift"]["changed_blocks"] == ["policy"]
    assert rows["UpstreamDrift"]["status"] == "current"
    assert rows["UpstreamDrift"]["changed_blocks"] == []
    assert rows["AffineDrift"]["expected_digest"] != rows["AffineDrift"]["observed_digest"]
    assert rows["UpstreamDrift"]["expected_digest"] == rows["UpstreamDrift"]["observed_digest"]
    assert rows["UpstreamDrift"]["expected_blocks"]["policy"]
    assert rows["UpstreamDrift"]["observed_blocks"]["policy"] == rows["UpstreamDrift"]["expected_blocks"]["policy"]
    assert rows["UpstreamDrift"]["last_seen_at"].endswith("+00:00")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [(None, "unavailable"), ("", "unavailable")],
)
@pytest.mark.asyncio
async def test_reports_missing_canonical_source_as_unavailable(payload: str | None, expected: str) -> None:
    async def fetch(_repo: str, _path: str) -> str | None:
        return payload

    report = await build_agents_sync_report(fetch, ["UpstreamDrift"])
    assert report["status"] == expected


@pytest.mark.asyncio
async def test_missing_repo_agents_is_unknown_not_current() -> None:
    async def fetch(repo: str, path: str) -> str | None:
        if repo == "Repository_Management":
            return "<!-- BEGIN FLEET-MANAGED: x -->\nvalue\n<!-- END FLEET-MANAGED: x -->"
        return None

    report = await build_agents_sync_report(fetch, ["UpstreamDrift"])
    row = report["repositories"][0]
    assert row["repo"] == "UpstreamDrift"
    assert row["status"] == "unavailable"
    assert row["expected_digest"] == report["source_digest"]
    assert row["observed_digest"] is None
    assert row["observed_blocks"] is None
    assert row["last_seen_at"] is None


@pytest.mark.asyncio
async def test_access_failure_is_exposed_as_unavailable() -> None:
    async def fetch(_repo: str, _path: str) -> str | None:
        raise HTTPException(status_code=403, detail="forbidden")

    report = await build_agents_sync_report(fetch, ["UpstreamDrift"])
    assert report["status"] == "unavailable"
    assert report["reason"] == "Repository metadata access is unavailable (HTTP 403)."


@pytest.mark.asyncio
async def test_empty_repository_inventory_is_unavailable() -> None:
    async def fetch(repo: str, path: str) -> str | None:
        return "<!-- BEGIN FLEET-MANAGED: x -->\nvalue\n<!-- END FLEET-MANAGED: x -->"

    report = await build_agents_sync_report(fetch, [])
    assert report["status"] == "unavailable"
    assert report["reason"] == "No repositories are configured for sync comparison."


@pytest.mark.asyncio
async def test_unreachable_repository_is_reported_unavailable_not_as_drift() -> None:
    async def fetch(repo: str, _path: str) -> str | None:
        if repo == "Repository_Management":
            return "<!-- BEGIN FLEET-MANAGED: x -->\nvalue\n<!-- END FLEET-MANAGED: x -->"
        raise HTTPException(status_code=403, detail="private repository")

    report = await build_agents_sync_report(fetch, ["Tools_Private"])
    row = report["repositories"][0]
    assert report["status"] == "unavailable"
    assert row["repo"] == "Tools_Private"
    assert row["status"] == "unavailable"
    assert row["observed_digest"] is None
    assert row["changed_blocks"] == []
    assert row["last_seen_at"] is None
