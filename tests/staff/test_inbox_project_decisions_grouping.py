"""Tests for project decisions grouping per repository in staff inbox (Workstream G)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from projects import service as proj_service
from staff import inbox


@pytest.mark.unit
def test_project_decisions_one_item_per_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = ["UpstreamDrift", "Runner_Dashboard"]

    async def overview(repo: str) -> dict[str, Any]:
        if repo == "UpstreamDrift":
            return {
                "repo": repo,
                "decisions_needed": [
                    "Approve modal damping model",
                    "Choose telemetry exporter",
                    "Decide on cache eviction strategy",
                ],
                "generated_at": "2026-09-27T12:00:00Z",
            }
        return {
            "repo": repo,
            "decisions_needed": ["Update CI matrix"],
            "generated_at": "2026-09-27T12:00:00Z",
        }

    monkeypatch.setattr(proj_service, "configured_repos", lambda: repos)
    monkeypatch.setattr(proj_service, "project_overview", overview)

    items = asyncio.run(inbox._collect_project_decisions())  # noqa: SLF001

    assert len(items) == 2
    drift_item = next(i for i in items if i.metadata["repo"] == "UpstreamDrift")
    assert drift_item.id == "project_dec_UpstreamDrift"
    assert drift_item.title == "UpstreamDrift: 3 decisions needed"
    assert len(drift_item.details) == 3
    assert drift_item.severity == "low"
    assert drift_item.link == "/projects/UpstreamDrift"

    rd_item = next(i for i in items if i.metadata["repo"] == "Runner_Dashboard")
    assert rd_item.id == "project_dec_Runner_Dashboard"
    assert rd_item.title == "Runner_Dashboard: 1 decision needed"
    assert rd_item.details == ["Update CI matrix"]
    assert rd_item.severity == "low"


@pytest.mark.unit
def test_project_decisions_urgent_escalates_severity(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = ["Tools"]

    async def overview(repo: str) -> dict[str, Any]:
        return {
            "repo": repo,
            "decisions_needed": [
                "Routine dependency bump",
                "[URGENT] Security patch approval for OpenSSL",
            ],
            "generated_at": "2026-09-27T12:00:00Z",
        }

    monkeypatch.setattr(proj_service, "configured_repos", lambda: repos)
    monkeypatch.setattr(proj_service, "project_overview", overview)

    items = asyncio.run(inbox._collect_project_decisions())  # noqa: SLF001

    assert len(items) == 1
    assert items[0].severity == "high"


@pytest.mark.unit
def test_project_decisions_empty_repo_produces_no_item(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = ["EmptyRepo"]

    async def overview(repo: str) -> dict[str, Any]:
        return {"repo": repo, "decisions_needed": [], "generated_at": "2026-09-27T12:00:00Z"}

    monkeypatch.setattr(proj_service, "configured_repos", lambda: repos)
    monkeypatch.setattr(proj_service, "project_overview", overview)

    items = asyncio.run(inbox._collect_project_decisions())  # noqa: SLF001
    assert items == []
