"""Fleet Job Board S3: ``POST /api/coordination/jobs/claim`` (#2784, slice 1)."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from coordination import claims as claims_mod
from coordination.jobs import JobIneligibleError
from coordination_fake_rm import FakeRM, bot, claim_status, install, issue as _issue, lease_result, release_result
from fastapi.testclient import TestClient

_REMOTE = ("100.64.0.9", 51000)
_XHR = {"X-Requested-With": "XMLHttpRequest"}
_BOT = {"Authorization": "Bearer bot-token", **_XHR}
TOOLS = "D-sorganization/Tools"


def _evidence(issues: dict[str, dict[int, dict[str, Any]]]) -> Any:
    def fetch(path: str) -> Any:
        if "/issues/" in path and path.count("/") >= 5:
            repo_full = path.split("/repos/", 1)[1].split("/issues/", 1)[0]
            number = int(path.rsplit("/", 1)[-1])
            return issues.get(repo_full, {}).get(number, {"state": "closed"})
        if path.endswith("/issues?state=open&per_page=100"):
            repo_full = path.split("/repos/", 1)[1].split("/issues", 1)[0]
            return list(issues.get(repo_full, {}).values())
        if path.endswith("/pulls?state=all&per_page=100"):
            return []
        return []

    return fetch


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    principals = {"bot-token": bot("agent-codex")}
    yield from install(tmp_path, monkeypatch, principals, repos="Tools")


@pytest.fixture
def client(rm: FakeRM) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=_REMOTE)


def _open_issue(number: int = 55, *, labels: list[str] | None = None) -> dict[str, Any]:
    payload = _issue(number, labels=labels or [])
    payload["state"] = "open"
    return payload


@pytest.mark.unit
def test_ineligible_job_claim_409_with_reason(
    rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _open_issue(55, labels=["blocked"])
    monkeypatch.setattr(
        claims_mod,
        "evidence_fetch",
        _evidence({TOOLS: {55: payload}}),
    )
    rm.respond("check_agent_claim", claim_status(False))
    body = {"session": "codex-55", "repo": "Tools", "issue": 55, "agent": "codex", "intent": "ship"}
    resp = client.post("/api/coordination/jobs/claim", json=body, headers=_BOT)
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "blocked" in detail["reason"]
    assert "post_agent_lease" not in [name for name, _ in rm.calls()]


@pytest.mark.unit
def test_claim_check_fail_open_returns_502_and_posts_nothing(
    rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claims_mod, "evidence_fetch", _evidence({TOOLS: {56: _open_issue(56)}}))
    rm.respond("check_agent_claim", claim_status(False, "", "error:Timeout"))
    body = {"session": "codex-56", "repo": "Tools", "issue": 56, "agent": "codex", "intent": "ship"}
    resp = client.post("/api/coordination/jobs/claim", json=body, headers=_BOT)
    assert resp.status_code == 502, resp.text
    assert "post_agent_lease" not in [name for name, _ in rm.calls()]


@pytest.mark.unit
def test_concurrent_claims_exactly_one_wins(
    rm: FakeRM, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coordination import jobs as jobs_mod

    monkeypatch.setattr(claims_mod, "evidence_fetch", _evidence({TOOLS: {57: _open_issue(57)}}))
    rm.respond("post_agent_lease", lease_result())
    rm.respond("release_agent_lease", release_result())
    posts = {"n": 0}
    winner_session = {"id": ""}
    real_post = claims_mod.post_lease

    def counting_post(*args: Any, **kwargs: Any) -> dict[str, Any]:
        posts["n"] += 1
        winner_session["id"] = kwargs.get("session") or args[3]
        return real_post(*args, **kwargs)

    monkeypatch.setattr(claims_mod, "post_lease", counting_post)

    def fake_check(repo: str, issue: int) -> dict[str, Any]:
        if posts["n"] == 0:
            return {"available": True, "held": False, "agent": "", "session": "", "reason": ""}
        return {
            "available": True,
            "held": True,
            "agent": "codex",
            "session": winner_session["id"],
            "reason": "active lease",
            "expires_at": "2026-09-23T12:00:00+00:00",
        }

    monkeypatch.setattr(claims_mod, "check", fake_check)

    results: list[str] = []

    def attempt(session: str) -> None:
        try:
            jobs_mod.claim_job("Tools", 57, agent="codex", session=session, intent="race")
            results.append("ok")
        except (claims_mod.ClaimHeldError, JobIneligibleError):
            results.append("held")

    t1 = threading.Thread(target=attempt, args=("codex-57a",))
    t2 = threading.Thread(target=attempt, args=("codex-57b",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert sorted(results) == ["held", "ok"]
