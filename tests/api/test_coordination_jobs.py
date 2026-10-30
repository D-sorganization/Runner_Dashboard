"""Fleet Job Board S1: ``GET /api/coordination/jobs`` (issue #2782, epic #2781). Read-only (S3 claims)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from coordination import claims as claims_mod
from coordination_fake_rm import FakeRM, bot, claim_status, install
from coordination_fake_rm import issue as _issue
from coordination_fake_rm import pr as _pr
from fastapi.testclient import TestClient
from priorities import service as priorities_service

_REMOTE = ("100.64.0.9", 51000)
TOOLS = "D-sorganization/Tools"
GAMES = "D-sorganization/Games"


def _fake_evidence(issues: dict[str, list[dict[str, Any]]], pulls: dict[str, list[dict[str, Any]]]) -> Any:
    calls: list[str] = []

    def fetch(path: str) -> Any:
        calls.append(path)
        for repo_full, repo_issues in issues.items():
            if path == f"/repos/{repo_full}/issues?state=open&per_page=100":
                return repo_issues
        return next((p for r, p in pulls.items() if path == f"/repos/{r}/pulls?state=all&per_page=100"), [])

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
    monkeypatch.setattr(claims_mod, "evidence_fetch", _fake_evidence(issues, pulls))
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
    monkeypatch.setattr(claims_mod, "evidence_fetch", _fake_evidence(issues, {}))
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
    monkeypatch.setattr(claims_mod, "evidence_fetch", _fake_evidence(issues, {}))
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
    monkeypatch.setattr(claims_mod, "evidence_fetch", fetch)
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
