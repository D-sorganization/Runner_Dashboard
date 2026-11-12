"""Fleet Job Board S2: hold and gate filters (issue #2783, epic #2781)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from coordination import claims as claims_mod
from coordination_fake_rm import FakeRM, bot, claim_status, install
from coordination_fake_rm import issue as _issue
from fastapi.testclient import TestClient
from staff.holds import Hold, HoldsList

_REMOTE = ("100.64.0.9", 51000)
RD = "D-sorganization/Runner_Dashboard"


def _fake_evidence(issues: list[dict[str, Any]]) -> Any:
    def fetch(path: str) -> Any:
        if path == f"/repos/{RD}/issues?state=open&per_page=100":
            return issues
        return []

    return fetch


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    principals = {"bot-token": bot("agent-codex")}
    yield from install(tmp_path, monkeypatch, principals, repos="Runner_Dashboard")


@pytest.fixture
def client(rm: FakeRM) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=_REMOTE)


def _schedule_hold(hold_id: str, text: str) -> Hold:
    return Hold(id=hold_id, text=text, applies_to=["repo:Runner_Dashboard"], active=True, kind="schedule")


@pytest.mark.unit
def test_rd_2657_deploy_release_hold_excludes_with_reason(
    rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    issues = [_issue(100, repo="Runner_Dashboard", labels=["hold:rd-2657"]), _issue(101, repo="Runner_Dashboard")]
    monkeypatch.setattr(claims_mod, "evidence_fetch", _fake_evidence(issues))
    rm.respond("check_agent_claim", claim_status(False))
    HoldsList().replace([_schedule_hold("rd-2657", "RD #2657: no deploy or release").to_dict()])

    body = client.get("/api/coordination/jobs?repo=Runner_Dashboard&include_excluded=true").json()

    assert body["available"] is True
    assert [j["issue"] for j in body["jobs"]] == [101]
    reasons = {e["issue"]: e["reason"] for e in body["excluded"]}
    assert reasons[100] == "hold: RD #2657: no deploy or release"


@pytest.mark.unit
def test_sidekick_packaging_hold_excludes_with_reason(
    rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    issues = [_issue(110, repo="Runner_Dashboard", labels=["hold:sidekick-packaging"])]
    monkeypatch.setattr(claims_mod, "evidence_fetch", _fake_evidence(issues))
    rm.respond("check_agent_claim", claim_status(False))
    HoldsList().replace([_schedule_hold("sidekick-packaging", "Sidekick packaging HOLD stays").to_dict()])

    body = client.get("/api/coordination/jobs?repo=Runner_Dashboard&include_excluded=true").json()

    assert body["jobs"] == []
    reasons = {e["issue"]: e["reason"] for e in body["excluded"]}
    assert reasons[110] == "hold: Sidekick packaging HOLD stays"


@pytest.mark.unit
def test_c3_hold_excludes_with_reason(rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    issues = [_issue(120, repo="Runner_Dashboard", labels=["hold:c3"])]
    monkeypatch.setattr(claims_mod, "evidence_fetch", _fake_evidence(issues))
    rm.respond("check_agent_claim", claim_status(False))
    HoldsList().replace([_schedule_hold("c3", "Preserve the C3 gate").to_dict()])

    body = client.get("/api/coordination/jobs?repo=Runner_Dashboard&include_excluded=true").json()

    assert body["jobs"] == []
    reasons = {e["issue"]: e["reason"] for e in body["excluded"]}
    assert reasons[120] == "hold: Preserve the C3 gate"


@pytest.mark.unit
def test_assessment_board_gate_excludes_with_reason(
    rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    issues = [
        _issue(130, repo="Runner_Dashboard", labels=["code-request", "code-request:board_review"]),
        _issue(131, repo="Runner_Dashboard"),
    ]
    monkeypatch.setattr(claims_mod, "evidence_fetch", _fake_evidence(issues))
    rm.respond("check_agent_claim", claim_status(False))

    body = client.get("/api/coordination/jobs?repo=Runner_Dashboard&include_excluded=true").json()

    assert [j["issue"] for j in body["jobs"]] == [131]
    reasons = {e["issue"]: e["reason"] for e in body["excluded"]}
    assert reasons[130] == "gate: code-request:board_review (Assessment Board review not yet cleared)"


@pytest.mark.unit
def test_guardrail_hold_does_not_exclude(rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    issues = [_issue(140, repo="Runner_Dashboard", labels=["hold:rd-2657"])]
    monkeypatch.setattr(claims_mod, "evidence_fetch", _fake_evidence(issues))
    rm.respond("check_agent_claim", claim_status(False))
    guardrail = Hold(
        id="rd-2657", text="RD #2657: no deploy or release", applies_to=["repo:Runner_Dashboard"],
        active=True, kind="guardrail",
    )  # fmt: skip
    HoldsList().replace([guardrail.to_dict()])

    body = client.get("/api/coordination/jobs?repo=Runner_Dashboard").json()

    assert [j["issue"] for j in body["jobs"]] == [140]


@pytest.mark.unit
def test_unreadable_holds_list_returns_unavailable(
    rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    issues = [_issue(150, repo="Runner_Dashboard")]
    monkeypatch.setattr(claims_mod, "evidence_fetch", _fake_evidence(issues))
    rm.respond("check_agent_claim", claim_status(False))
    HoldsList().path.write_text("not json", encoding="utf-8")

    body = client.get("/api/coordination/jobs?repo=Runner_Dashboard&include_excluded=true").json()

    assert body["available"] is False
    assert body["jobs"] == []
    assert any("holds list unreadable" in w for w in body["warnings"])
