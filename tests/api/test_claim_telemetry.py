"""Claims-path telemetry (issue #4595) — p50/p95 latency, subprocess call counts,
blocked lane exits and expired holds, plus the JSONL baseline recorder.

The unit block exercises the ``coordination.claim_telemetry`` contract directly;
the wiring block drives the coordination API through ``FakeRM`` and asserts the
claims path actually records what it observes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from coordination import claim_telemetry as telemetry
from coordination_fake_rm import FakeRM, bot, claim_status, install
from fastapi.testclient import TestClient

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_BOT = {"Authorization": "Bearer bot-token", **_XHR}


@pytest.fixture(autouse=True)
def _reset_telemetry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    telemetry.reset()
    monkeypatch.delenv("CLAIMS_BASELINE_FILE", raising=False)
    yield
    telemetry.reset()


# ── module contract ───────────────────────────────────────────────────────


def test_summary_starts_empty() -> None:
    summary = telemetry.summary()
    assert summary["claims_requests_total"] == 0
    assert summary["p50_s"] is None
    assert summary["p95_s"] is None
    assert summary["by_result"] == {}
    assert summary["rm_subprocess_calls"] == {}
    assert summary["blocked_exits"] == {}


def test_p50_p95_from_reservoir() -> None:
    for i in range(1, 101):
        telemetry.record_claim_check(i / 100.0, "available")
    summary = telemetry.summary()
    assert summary["claims_requests_total"] == 100
    assert summary["p50_s"] == pytest.approx(0.50, abs=0.02)
    assert summary["p95_s"] == pytest.approx(0.95, abs=0.02)


def test_claim_check_classification_counts() -> None:
    for result in ("available", "held", "unavailable", "stale", "unavailable_budget"):
        telemetry.record_claim_check(1.0, result)
    assert telemetry.summary()["by_result"] == {
        "available": 1,
        "held": 1,
        "unavailable": 1,
        "stale": 1,
        "unavailable_budget": 1,
    }


def test_record_claim_check_rejects_unknown_result() -> None:
    with pytest.raises(ValueError):
        telemetry.record_claim_check(1.0, "nonsense")


def test_rm_subprocess_counts_and_timeouts() -> None:
    telemetry.record_rm_subprocess("check_agent_claim", 5.0, timed_out=False)
    telemetry.record_rm_subprocess("check_agent_claim", 120.0, timed_out=True)
    summary = telemetry.summary()
    assert summary["rm_subprocess_calls"] == {"check_agent_claim": 2}
    assert summary["rm_subprocess_timeouts"] == {"check_agent_claim": 1}
    assert summary["blocked_exits"] == {"timeout:check_agent_claim": 1}


def test_blocked_exits_and_expired_holds() -> None:
    telemetry.record_blocked_exit("held")
    telemetry.record_blocked_exit("held")
    telemetry.record_blocked_exit("budget_exceeded")
    telemetry.record_expired_hold(7200.0)
    summary = telemetry.summary()
    assert summary["blocked_exits"] == {"held": 2, "budget_exceeded": 1}
    assert summary["expired_holds_count"] == 1
    assert summary["expired_holds_max_age_s"] == pytest.approx(7200.0)


def test_jsonl_baseline_records_each_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAIMS_BASELINE_FILE", str(tmp_path / "claims.jsonl"))
    telemetry.record_claim_check(1.23, "available")
    telemetry.record_blocked_exit("held")
    records = [
        json.loads(line) for line in (tmp_path / "claims.jsonl").read_text(encoding="utf-8").splitlines() if line
    ]
    assert {rec["kind"] for rec in records} == {"claim_check", "blocked_exit"}
    claim = next(rec for rec in records if rec["kind"] == "claim_check")
    assert claim["result"] == "available"
    assert claim["duration_s"] == pytest.approx(1.23)


# ── baseline reporter ──────────────────────────────────────────────────────


def test_build_report_computes_percentiles_and_proxies() -> None:
    from scripts import claims_baseline

    records = [
        {"kind": "claim_check", "result": "available", "duration_s": float(d)}
        for d in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0)
    ]
    records += [
        {"kind": "rm_subprocess", "script": "check_agent_claim", "duration_s": 5.0, "timed_out": False},
        {"kind": "rm_subprocess", "script": "check_agent_claim", "duration_s": 120.0, "timed_out": True},
        {"kind": "blocked_exit", "reason": "held"},
        {"kind": "expired_hold", "age_s": 3600.0},
    ]
    report = claims_baseline.build_report(records)
    assert report["claims_requests_total"] == 10
    assert report["p50_s"] == pytest.approx(5.0, abs=0.6)
    assert report["p95_s"] == pytest.approx(10.0, abs=0.6)
    assert report["rm_subprocess_calls"] == {"check_agent_claim": 2}
    assert report["rm_subprocess_timeouts"] == {"check_agent_claim": 1}
    assert report["github_calls_per_request"] == pytest.approx(0.2)
    assert report["blocked_exits"] == {"held": 1}
    assert report["expired_holds_count"] == 1
    assert report["expired_holds_max_age_s"] == pytest.approx(3600.0)


def test_render_markdown_contains_key_numbers_and_no_target() -> None:
    from scripts import claims_baseline

    report = claims_baseline.build_report([{"kind": "claim_check", "result": "available", "duration_s": 1.0}])
    markdown = claims_baseline.render_markdown(report)
    assert "Claims path baseline (issue #4595)" in markdown
    assert "p50 of `GET /api/coordination/claims` latency" in markdown
    assert "1.000s" in markdown
    assert "No latency or GitHub-call target is declared here" in markdown


# ── wiring through the coordination API ────────────────────────────────────


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    principals = {"bot-token": bot("agent-codex")}
    yield from install(tmp_path, monkeypatch, principals, repos="Tools,Games")


@pytest.fixture
def client(rm: FakeRM) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=("100.64.0.9", 51000))


@pytest.mark.unit
def test_get_claims_records_latency_result_and_subprocess(rm: FakeRM, client: TestClient) -> None:
    rm.respond("check_agent_claim", claim_status(False))
    body = client.get("/api/coordination/claims?repo=Tools&issue=3").json()
    assert body["available"] is True
    summary = telemetry.summary()
    assert summary["claims_requests_total"] == 1
    assert summary["by_result"] == {"available": 1}
    assert summary["rm_subprocess_calls"] == {"check_agent_claim": 1}
    assert summary["p50_s"] is not None


@pytest.mark.unit
def test_get_claims_held_is_classified(rm: FakeRM, client: TestClient) -> None:
    rm.respond("check_agent_claim", claim_status(True, agent="codex", reason="claim:codex"))
    body = client.get("/api/coordination/claims?repo=Tools&issue=3").json()
    assert body["held"] is True
    assert telemetry.summary()["by_result"] == {"held": 1}


@pytest.mark.unit
def test_claim_timeout_records_blocked_exit(rm: FakeRM, client: TestClient) -> None:
    rm.respond("check_agent_claim", claim_status(False), rc=130)
    client.get("/api/coordination/claims?repo=Tools&issue=3")
    summary = telemetry.summary()
    assert summary["rm_subprocess_timeouts"] == {"check_agent_claim": 1}
    assert summary["blocked_exits"] == {"timeout:check_agent_claim": 1}


@pytest.mark.unit
def test_post_claim_held_records_blocked_exit(rm: FakeRM, client: TestClient) -> None:
    rm.respond("check_agent_claim", claim_status(True, agent="codex", reason="claim:codex"))
    body = {"agent": "codex", "session": "codex-1", "repo": "Tools", "issue": 3, "intent": "fix"}
    resp = client.post("/api/coordination/claims", json=body, headers=_BOT)
    assert resp.status_code == 409
    assert telemetry.summary()["blocked_exits"] == {"held": 1}
