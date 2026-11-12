"""Fleet Job Board S3 slice 2: ``POST /api/coordination/jobs/release`` + fence (#2784)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from coordination import claims as claims_mod
from coordination_fake_rm import FakeRM, bot, claim_status, install, lease_result, release_result
from coordination_fake_rm import issue as _issue
from fastapi.testclient import TestClient

_REMOTE = ("100.64.0.9", 51000)
_XHR = {"X-Requested-With": "XMLHttpRequest"}
_BOT = {"Authorization": "Bearer bot-token", **_XHR}


def _evidence(number: int) -> Any:
    payload = _issue(number, labels=[])
    payload["state"] = "open"

    def fetch(path: str) -> Any:
        if path.endswith(f"/issues/{number}"):
            return payload
        if path.endswith("/pulls?state=all&per_page=100"):
            return []
        return []

    return fetch


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    yield from install(tmp_path, monkeypatch, {"bot-token": bot("agent-codex")}, repos="Tools")


@pytest.fixture
def client(rm: FakeRM) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=_REMOTE)


@pytest.mark.unit
def test_release_with_stale_fence_refused(
    rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claims_mod, "evidence_fetch", _evidence(58))
    rm.respond("post_agent_lease", lease_result())
    posted = {"n": 0}
    real_post_lease = claims_mod.post_lease

    def post_lease(*a: Any, **k: Any) -> dict[str, Any]:
        posted["n"] += 1
        return real_post_lease(*a, **k)

    def check(repo: str, issue: int) -> dict[str, Any]:
        if posted["n"] == 0:
            return {"available": True, "held": False, "agent": "", "session": "", "reason": ""}
        return {
            "available": True,
            "held": True,
            "agent": "codex",
            "session": "codex-58",
            "reason": "active lease",
            "expires_at": None,
        }

    with pytest.MonkeyPatch.context() as claim_mp:
        claim_mp.setattr(claims_mod, "post_lease", post_lease)
        claim_mp.setattr(claims_mod, "check", check)
        claimed = client.post(
            "/api/coordination/jobs/claim",
            json={"session": "codex-58", "repo": "Tools", "issue": 58, "agent": "codex", "intent": "ship"},
            headers=_BOT,
        )
    assert claimed.status_code == 200, claimed.text
    fence = claimed.json()["fence"]
    rm.respond("check_agent_claim", {**claim_status(True, "codex", "active lease"), "session": "codex-58"})
    stale = {"session": "codex-58", "repo": "Tools", "issue": 58, "agent": "codex", "fence": fence + 99, "reason": "done"}
    assert client.post("/api/coordination/jobs/release", json=stale, headers=_BOT).status_code == 409
    rm.respond("release_agent_lease", release_result())
    assert client.post("/api/coordination/jobs/release", json={**stale, "fence": fence}, headers=_BOT).status_code == 200
