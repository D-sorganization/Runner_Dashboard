"""Fleet Job Board S4: TTL, heartbeat and expiry sweep (#2785)."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from coordination import claims as claims_mod
from coordination import jobs as jobs_mod
from coordination_fake_rm import FakeRM, bot, claim_status, install, issue as _issue, lease_result, release_result
from fastapi.testclient import TestClient

_REMOTE = ("100.64.0.9", 51000)
_XHR = {"X-Requested-With": "XMLHttpRequest"}
_BOT = {"Authorization": "Bearer bot-token", **_XHR}
TOOLS = "D-sorganization/Tools"


def _evidence(number: int) -> Any:
    payload = _issue(number, labels=[])
    payload["state"] = "open"
    list_path = f"/repos/{TOOLS}/issues?state=open&per_page=100"

    def fetch(path: str) -> Any:
        if path == list_path:
            return [payload]
        if path.endswith(f"/issues/{number}"):
            return payload
        if path.endswith("/pulls?state=all&per_page=100"):
            return []
        return []

    return fetch


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    principals = {"bot-token": bot("agent-codex")}
    yield from install(tmp_path, monkeypatch, principals, repos="Tools")


@pytest.fixture
def client(rm: FakeRM, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import activity as activity_mod
    import activity_producers
    from activity import ActivityStore, reset_activity_store
    from server import app  # noqa: PLC0415

    reset_activity_store()
    activity_producers._next_seq.clear()
    store = ActivityStore(tmp_path / "activity.sqlite3")
    monkeypatch.setattr(activity_producers, "get_activity_store", lambda: store)
    monkeypatch.setattr(activity_mod, "get_activity_store", lambda: store)
    return TestClient(app, raise_server_exceptions=False, client=_REMOTE)


def _claim(client: TestClient, rm: FakeRM, monkeypatch: pytest.MonkeyPatch, number: int) -> dict[str, Any]:
    monkeypatch.setattr(claims_mod, "evidence_fetch", _evidence(number))
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", lease_result())
    body = {
        "session": f"codex-{number}",
        "repo": "Tools",
        "issue": number,
        "agent": "codex",
        "intent": "ship",
        "ttl_hours": 0.1,
    }
    resp = client.post("/api/coordination/jobs/claim", json=body, headers=_BOT)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.unit
def test_unheartbeated_claim_released_and_relisted(
    rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    claimed = _claim(client, rm, monkeypatch, 70)
    past = datetime.now(UTC) + timedelta(hours=1)
    held = {**claim_status(True, "codex", "active lease"), "session": claimed["session"]}
    rm.respond("check_agent_claim", held)
    rm.respond("release_agent_lease", release_result())
    assert jobs_mod.sweep_expired_job_claims(now=past)["released"] == 1
    rm.respond("check_agent_claim", claim_status(False))
    monkeypatch.setattr(claims_mod, "evidence_fetch", _evidence(70))
    jobs = client.get("/api/coordination/jobs?repo=Tools").json()
    assert any(j["issue"] == 70 for j in jobs["jobs"])


@pytest.mark.unit
def test_heartbeated_claim_survives_sweep(rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    claimed = _claim(client, rm, monkeypatch, 71)
    held = {**claim_status(True, "codex", "active lease"), "session": claimed["session"]}
    rm.respond("check_agent_claim", held)
    hb = {"session": claimed["session"], "repo": "Tools", "issue": 71, "fence": claimed["fence"], "ttl_hours": 2}
    assert client.post("/api/coordination/jobs/heartbeat", json=hb, headers=_BOT).status_code == 200
    soon = datetime.now(UTC) + timedelta(minutes=30)
    assert jobs_mod.sweep_expired_job_claims(now=soon)["released"] == 0


@pytest.mark.unit
def test_sweep_error_on_one_issue_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    jobs_mod.reset_job_board_claim_state()
    for number, session in ((80, "codex-80"), (81, "codex-81")):
        with jobs_mod._fence_guard:  # noqa: SLF001
            jobs_mod._fences[jobs_mod._fence_key("Tools", number)] = 1
        jobs_mod._remember_lease(
            "Tools",
            number,
            agent="codex",
            session=session,
            fence=1,
            ttl_hours=0.1,
            expires_at=(now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        )

    def flaky_release(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if args[1] == 80:
            raise RuntimeError("boom")
        return {"released": True, "warnings": []}

    monkeypatch.setattr(jobs_mod, "release_job", flaky_release)
    result = jobs_mod.sweep_expired_job_claims(now=now)
    assert result["released"] == 1 and result["errors"]
