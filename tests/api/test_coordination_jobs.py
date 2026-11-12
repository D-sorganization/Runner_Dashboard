"""Fleet Job Board S1: ``GET /api/coordination/jobs`` (issue #2782, epic #2781). Read-only (S3 claims)."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from coordination import claims as claims_mod
from coordination import jobs as jobs_mod
from coordination_fake_rm import FakeRM, bot, claim_status, install, lease_result, patch_gh_list_fetch, release_result
from coordination_fake_rm import issue as _issue
from coordination_fake_rm import pr as _pr
from fastapi.testclient import TestClient
from priorities import service as priorities_service

_REMOTE = ("100.64.0.9", 51000)
_BOT = {"Authorization": "Bearer bot-token", "X-Requested-With": "XMLHttpRequest"}
TOOLS = "D-sorganization/Tools"
GAMES = "D-sorganization/Games"


def _fake_evidence(issues: dict[str, list[dict[str, Any]]], pulls: dict[str, list[dict[str, Any]]]) -> Any:
    calls: list[str] = []

    def fetch(path: str) -> Any:
        calls.append(path)
        for repo_full, repo_issues in issues.items():
            if path in (
                f"/repos/{repo_full}/issues?state=open&per_page=100",
                f"/repos/{repo_full}/issues?state=open",
            ):
                return repo_issues
        for repo_full, repo_pulls in pulls.items():
            if path in (
                f"/repos/{repo_full}/pulls?state=all&per_page=100",
                f"/repos/{repo_full}/pulls?state=all",
            ):
                return repo_pulls
        return []

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    principals = {"bot-token": bot("agent-codex")}
    yield from install(tmp_path, monkeypatch, principals, repos="Tools,Games")


@pytest.fixture
def client(rm: FakeRM) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=_REMOTE)


@pytest.mark.unit
def test_jobs_excludes_claimed_labelled_pr_and_hold_issues_with_reason(
    rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    issues = {
        TOOLS: [
            _issue(10, labels=["claim:codex"]),
            _issue(11, labels=["blocked"]),
            _issue(12),
            _issue(13),
        ]
    }
    pulls = {TOOLS: [_pr(50, closes=12)]}
    patch_gh_list_fetch(monkeypatch, _fake_evidence(issues, pulls))
    rm.respond("check_agent_claim", claim_status(False))

    body = client.get("/api/coordination/jobs?repo=Tools&include_excluded=true").json()

    assert body["available"] is True
    assert [j["issue"] for j in body["jobs"]] == [13]
    reasons = {e["issue"]: e["reason"] for e in body["excluded"]}
    assert reasons[10] == "carries claim:codex"
    assert reasons[11] == "carries hold label 'blocked'"
    assert reasons[12] == "open PR #50 already closes it"


@pytest.mark.unit
def test_jobs_claim_check_fail_open_is_excluded_with_warning(
    rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    issues = {TOOLS: [_issue(20)]}
    patch_gh_list_fetch(monkeypatch, _fake_evidence(issues, {}))
    rm.respond("check_agent_claim", claim_status(False, "", "error:RuntimeError: GitHub unreachable"))

    body = client.get("/api/coordination/jobs?repo=Tools&include_excluded=true").json()

    assert body["jobs"] == []
    assert any("claim check unavailable" in w and "20" in w for w in body["warnings"])
    assert body["excluded"][0]["issue"] == 20
    assert "claim check unavailable" in body["excluded"][0]["reason"]


@pytest.mark.unit
def test_jobs_sorted_by_priority_then_board(rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    issues = {
        TOOLS: [
            _issue(30, labels=["low"]),
            _issue(31, labels=["high"]),
            _issue(32, labels=["high"]),
            _issue(33),
        ]
    }
    patch_gh_list_fetch(monkeypatch, _fake_evidence(issues, {}))
    rm.respond("check_agent_claim", claim_status(False))
    monkeypatch.setattr(priorities_service, "top_priorities", lambda limit: [{"item": "ship it", "scope": "Tools #32"}])

    body = client.get("/api/coordination/jobs?repo=Tools").json()

    assert [j["issue"] for j in body["jobs"]] == [32, 31, 30, 33]
    assert [j["priority"] for j in body["jobs"]] == ["high", "high", "low", ""]


@pytest.mark.unit
def test_jobs_repo_filter_and_limit_bounds(rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    issues = {
        TOOLS: [_issue(40), _issue(41)],
        GAMES: [_issue(42, repo="Games")],
    }
    fetch = _fake_evidence(issues, {})
    patch_gh_list_fetch(monkeypatch, fetch)
    rm.respond("check_agent_claim", claim_status(False))

    body = client.get("/api/coordination/jobs?repo=Tools").json()
    assert {j["issue"] for j in body["jobs"]} == {40, 41}
    assert not any("Games" in call for call in fetch.calls)  # type: ignore[attr-defined]

    limited = client.get("/api/coordination/jobs?repo=Tools&limit=1").json()
    assert len(limited["jobs"]) == 1

    owner_prefixed = client.get("/api/coordination/jobs?repo=D-sorganization/Tools").json()
    assert {j["issue"] for j in owner_prefixed["jobs"]} == {40, 41}

    assert client.get("/api/coordination/jobs?limit=0").status_code == 422
    assert client.get("/api/coordination/jobs?limit=201").status_code == 422


def _claim_issue(number: int, *, labels: list[str] | None = None) -> dict[str, Any]:
    payload = _issue(number, labels=labels or [])
    payload["state"] = "open"
    return payload


def _claim_fetch(number: int, *, labels: list[str] | None = None) -> Any:
    payload = _claim_issue(number, labels=labels)

    def fetch(path: str) -> Any:
        if path.endswith(f"/issues/{number}"):
            return payload
        if path.endswith("/pulls?state=all&per_page=100"):
            return []
        return []

    return fetch


@pytest.mark.unit
def test_jobs_claim_ineligible_409(rm: FakeRM, monkeypatch: pytest.MonkeyPatch) -> None:
    from coordination import jobs as jobs_mod
    from coordination.jobs import JobIneligibleError

    patch_gh_list_fetch(monkeypatch, _claim_fetch(55, labels=["blocked"]))
    rm.respond("check_agent_claim", claim_status(False))
    with pytest.raises(JobIneligibleError, match="blocked"):
        jobs_mod.claim_job("Tools", 55, agent="codex", session="codex-55", intent="ship")
    assert "post_agent_lease" not in [n for n, _ in rm.calls()]


@pytest.mark.unit
def test_jobs_claim_fail_open_502(rm: FakeRM, monkeypatch: pytest.MonkeyPatch) -> None:
    from coordination import jobs as jobs_mod
    from coordination.board import RMScriptError

    patch_gh_list_fetch(monkeypatch, _claim_fetch(56))
    rm.respond("check_agent_claim", claim_status(False, "", "error:Timeout"))
    with pytest.raises(RMScriptError):
        jobs_mod.claim_job("Tools", 56, agent="codex", session="codex-56", intent="ship")
    assert "post_agent_lease" not in [n for n, _ in rm.calls()]


@pytest.mark.unit
def test_jobs_claim_post_write_check_unavailable_is_not_success(rm: FakeRM, monkeypatch: pytest.MonkeyPatch) -> None:
    """#4222: a post-write ``check_agent_claim`` that fails open must never report a successful claim."""
    from coordination import jobs as jobs_mod
    from coordination.board import RMScriptError

    patch_gh_list_fetch(monkeypatch, _claim_fetch(58))
    rm.respond("post_agent_lease", lease_result())
    posted = {"n": 0}
    real_post_lease = claims_mod.post_lease

    def post_lease(*a: Any, **k: Any) -> dict[str, Any]:
        posted["n"] += 1
        return real_post_lease(*a, **k)

    def check(repo: str, issue: int) -> dict[str, Any]:
        if posted["n"] == 0:
            return {"available": True, "held": False, "agent": "", "session": "", "reason": ""}
        return {"available": False, "reason": "error:Timeout", "held": None}

    monkeypatch.setattr(claims_mod, "post_lease", post_lease)
    monkeypatch.setattr(claims_mod, "check", check)

    with pytest.raises(RMScriptError):
        jobs_mod.claim_job("Tools", 58, agent="codex", session="codex-58", intent="ship")
    assert jobs_mod._current_fence("Tools", 58) == 0


@pytest.mark.unit
def test_jobs_claim_post_write_check_unheld_is_not_success(rm: FakeRM, monkeypatch: pytest.MonkeyPatch) -> None:
    """#4222: a verified-unheld post-write read means the lease never stuck; no claim, no fence, no activity."""
    from coordination import jobs as jobs_mod
    from coordination.board import RMScriptError

    patch_gh_list_fetch(monkeypatch, _claim_fetch(59))
    rm.respond("post_agent_lease", lease_result())
    monkeypatch.setattr(
        claims_mod,
        "check",
        lambda repo, issue: {"available": True, "held": False, "agent": "", "session": "", "reason": ""},
    )

    with pytest.raises(RMScriptError):
        jobs_mod.claim_job("Tools", 59, agent="codex", session="codex-59", intent="ship")
    assert jobs_mod._current_fence("Tools", 59) == 0


@pytest.mark.unit
def test_jobs_concurrent_claim_one_wins(rm: FakeRM, monkeypatch: pytest.MonkeyPatch) -> None:
    from coordination import jobs as jobs_mod
    from coordination.jobs import JobIneligibleError

    patch_gh_list_fetch(monkeypatch, _claim_fetch(57))
    rm.respond("post_agent_lease", lease_result())
    rm.respond("release_agent_lease", release_result())
    posts = {"n": 0}
    winner = {"session": ""}
    real = claims_mod.post_lease

    def post(*a: Any, **k: Any) -> dict[str, Any]:
        posts["n"] += 1
        winner["session"] = k.get("session") or a[3]
        return real(*a, **k)

    monkeypatch.setattr(claims_mod, "post_lease", post)
    monkeypatch.setattr(
        claims_mod,
        "check",
        lambda repo, issue: {
            "available": True,
            "held": posts["n"] > 0,
            "agent": "codex",
            "session": winner["session"],
            "reason": "lease",
        }
        if posts["n"] > 0
        else {"available": True, "held": False, "agent": "", "session": "", "reason": ""},
    )
    out: list[str] = []

    def go(sess: str) -> None:
        try:
            jobs_mod.claim_job("Tools", 57, agent="codex", session=sess, intent="race")
            out.append("ok")
        except (claims_mod.ClaimHeldError, JobIneligibleError):
            out.append("held")

    t1, t2 = threading.Thread(target=go, args=("codex-57a",)), threading.Thread(target=go, args=("codex-57b",))
    t1.start()
    t2.start()
    t1.join(10)
    t2.join(10)
    assert sorted(out) == ["held", "ok"]


def _ttl_fetch(number: int) -> Any:
    payload = _claim_issue(number)

    def fetch(path: str) -> Any:
        open_issues = (
            f"/repos/{TOOLS}/issues?state=open&per_page=100",
            f"/repos/{TOOLS}/issues?state=open",
        )
        if path in open_issues:
            return [payload]
        if path.endswith(f"/issues/{number}"):
            return payload
        if path.endswith("/pulls?state=all") or path.endswith("/pulls?state=all&per_page=100"):
            return []
        return []

    return fetch


def _post_claim(client: TestClient, rm: FakeRM, monkeypatch: pytest.MonkeyPatch, number: int) -> dict[str, Any]:
    """Claim ``number`` for ``codex-<number>``, modelling a real post-write read: unheld, then held by us.

    Only patches ``claims_mod.check`` for the claim call itself (via a scoped ``MonkeyPatch``), so callers
    are free to drive later reads (sweep, re-list) through ``rm.respond("check_agent_claim", ...)`` as before.
    """
    patch_gh_list_fetch(monkeypatch, _ttl_fetch(number))
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
            "session": f"codex-{number}",
            "reason": "active lease",
            "expires_at": None,
        }

    with pytest.MonkeyPatch.context() as claim_mp:
        claim_mp.setattr(claims_mod, "post_lease", post_lease)
        claim_mp.setattr(claims_mod, "check", check)
        resp = client.post(
            "/api/coordination/jobs/claim",
            json={
                "session": f"codex-{number}",
                "repo": "Tools",
                "issue": number,
                "agent": "codex",
                "intent": "ship",
                "ttl_hours": 0.1,
            },
            headers=_BOT,
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.unit
def test_jobs_ttl_sweep_relists_unheartbeated(rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    claimed = _post_claim(client, rm, monkeypatch, 70)
    held = {**claim_status(True, "codex", "active lease"), "session": claimed["session"]}
    rm.respond("check_agent_claim", held)
    rm.respond("release_agent_lease", release_result())
    assert jobs_mod.sweep_expired_job_claims(now=datetime.now(UTC) + timedelta(hours=1))["released"] == 1
    rm.respond("check_agent_claim", claim_status(False))
    patch_gh_list_fetch(monkeypatch, _ttl_fetch(70))
    assert any(j["issue"] == 70 for j in client.get("/api/coordination/jobs?repo=Tools").json()["jobs"])


@pytest.mark.unit
def test_jobs_heartbeat_extends_ttl(rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    claimed = _post_claim(client, rm, monkeypatch, 71)
    held = {**claim_status(True, "codex", "active lease"), "session": claimed["session"]}
    rm.respond("check_agent_claim", held)
    hb = {"session": claimed["session"], "repo": "Tools", "issue": 71, "fence": claimed["fence"], "ttl_hours": 2}
    assert client.post("/api/coordination/jobs/heartbeat", json=hb, headers=_BOT).status_code == 200
    assert jobs_mod.sweep_expired_job_claims(now=datetime.now(UTC) + timedelta(minutes=30))["released"] == 0
