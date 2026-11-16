"""API contract for GET /api/kanban/github (#4319)."""

from __future__ import annotations

from typing import Any

import kanban_github_ingestion as kgi
from kanban_github_ingestion import (
    KanbanGithubBoardItem,
    KanbanGithubMetrics,
    KanbanGithubSyncResult,
    SourceFreshness,
)


def test_kanban_github_api_payload(monkeypatch, make_authed_client, viewer_principal) -> None:
    async def _fake_sync(owner: str, repo: str, **kwargs: Any) -> KanbanGithubSyncResult:
        return KanbanGithubSyncResult(
            owner=owner,
            repo=repo,
            last_sync="2026-11-11T12:00:00Z",
            cache_ttl_seconds=60.0,
            sync_mode="cold",
            metrics=KanbanGithubMetrics(1, 0, 1, 40, False),
            freshness={"issues": SourceFreshness("ok", "2026-11-11T12:00:00Z")},
            items=(
                KanbanGithubBoardItem(
                    issue_number=1,
                    snapshot={"owner": owner, "repo": repo, "issue": {"number": 1, "state": "open"}},
                    card={"column": "issue_open", "issue_number": 1},
                ),
            ),
        )

    monkeypatch.setattr(kgi, "sync_repo_github_kanban", _fake_sync)
    client = make_authed_client(viewer_principal)
    response = client.get("/api/kanban/github", params={"owner": "o", "repo": "r"})
    assert response.status_code == 200
    body = response.json()
    assert body["last_sync"] == "2026-11-11T12:00:00Z"
    assert body["metrics"]["github_api_calls"] == 1
    assert body["freshness"]["issues"]["status"] == "ok"
    assert body["items"][0]["card"]["column"] == "issue_open"
