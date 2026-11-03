"""``GET /api/directives/drain`` (#3915 slice 1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def drain_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("STAFF_DIRECTIVES_FILE", str(tmp_path / "directives.json"))
    monkeypatch.delenv("DRAIN_OPEN_PR_THRESHOLD", raising=False)
    from identity import require_fleet_peer
    from priorities.directives import get_directives
    from routers import drain

    get_directives().replace(
        [{"text": "Drain mode on Gasification_Model", "repo": "Gasification_Model", "expires": "2099-01-01T00:00:00Z"}],
        author="op",
    )
    counts: dict[str, int] = {"Gasification_Model": 44}
    calls: list[str] = []

    def fake_count(repo: str) -> int:
        calls.append(repo)
        if repo not in counts:
            raise RuntimeError("gh api failed")
        return counts[repo]

    monkeypatch.setattr(drain, "open_pr_count", fake_count)
    app = FastAPI()
    app.include_router(drain.router)
    app.dependency_overrides[require_fleet_peer] = lambda: "test-peer"
    with TestClient(app) as client:
        yield client, counts, calls


@pytest.mark.parametrize(
    ("count", "drained", "reason"),
    [(44, True, "active"), (12, False, "below_threshold"), (None, True, "count_unavailable")],
)
def test_drain_status_for_directive_repo(drain_client: Any, count: int | None, drained: bool, reason: str) -> None:
    client, counts, _calls = drain_client
    counts.pop("Gasification_Model")
    if count is not None:
        counts["Gasification_Model"] = count
    resp = client.get("/api/directives/drain", params={"repo": "Gasification_Model"})
    assert resp.status_code == 200
    body = resp.json()
    assert (body["drained"], body["reason"], body["open_pr_count"]) == (drained, reason, count)
    assert (body["threshold"], body["expires"]) == (20, "2099-01-01T00:00:00Z")


def test_undrained_repo_skips_the_github_lookup(drain_client: Any) -> None:
    client, _counts, calls = drain_client
    body = client.get("/api/directives/drain", params={"repo": "Runner_Dashboard"}).json()
    assert body["drained"] is False
    assert body["reason"] == "no_directive"
    assert body["open_pr_count"] is None
    assert calls == []


@pytest.mark.parametrize("repo", ["", "../etc", "owner/repo", "a" * 101])
def test_bad_repo_is_422(drain_client: Any, repo: str) -> None:
    client, _counts, calls = drain_client
    resp = client.get("/api/directives/drain", params={"repo": repo})
    assert resp.status_code == 422
    assert calls == []


def test_requires_fleet_peer_when_hub_token_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_DIRECTIVES_FILE", str(tmp_path / "directives.json"))
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fixture-hub-fleet-token")
    from routers import drain

    app = FastAPI()
    app.include_router(drain.router)
    with TestClient(app) as client:
        resp = client.get("/api/directives/drain", params={"repo": "Gasification_Model"})
    assert resp.status_code in (401, 403)
