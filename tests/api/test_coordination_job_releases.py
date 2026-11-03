"""``GET /api/coordination/jobs/releases`` projects job_board activity (#2785 slice)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from coordination_fake_rm import FakeRM, bot, claim_status, install, lease_result, release_result
from fastapi.testclient import TestClient

_REMOTE = ("100.64.0.9", 51000)
_XHR = {"X-Requested-With": "XMLHttpRequest"}
_BOT = {"Authorization": "Bearer bot-token", **_XHR}


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    principals = {"bot-token": bot("agent-codex")}
    yield from install(tmp_path, monkeypatch, principals, repos="Tools")


@pytest.fixture
def activity_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    import activity as activity_mod
    import activity_producers
    from activity import ActivityStore, reset_activity_store

    reset_activity_store()
    activity_producers._next_seq.clear()
    real_store = ActivityStore(tmp_path / "activity.sqlite3")
    monkeypatch.setattr(activity_producers, "get_activity_store", lambda: real_store)
    monkeypatch.setattr(activity_mod, "get_activity_store", lambda: real_store)
    return real_store


@pytest.fixture
def client(rm: FakeRM, activity_store: Any) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=_REMOTE)


@pytest.mark.unit
def test_job_releases_lists_manual_release_after_claim_release(
    rm: FakeRM, client: TestClient, activity_store: Any
) -> None:
    claim_body = {"session": "codex-12", "repo": "Tools", "issue": 12, "agent": "codex", "intent": "implement"}
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", lease_result())
    assert client.post("/api/coordination/claims", json=claim_body, headers=_BOT).status_code == 200

    rel_body = {"session": "codex-12", "repo": "Tools", "issue": 12, "agent": "codex", "reason": "done"}
    rm.respond("release_agent_lease", release_result())
    assert client.post("/api/coordination/claims/release", json=rel_body, headers=_BOT).status_code == 200

    page = activity_store.scan_recent(limit=50)
    kinds = [e.kind for e in page if e.source == "job_board"]
    assert kinds == ["release", "claim"]

    resp = client.get("/api/coordination/jobs/releases")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["releases"]
    row = body["releases"][0]
    assert row["repo"] == "Tools"
    assert row["issue"] == 12
    assert row["session"] == "codex-12"
    assert row["agent"] == "codex"
    assert row["kind"] == "release"
    assert "released_at" in row
