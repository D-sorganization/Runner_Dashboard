"""API regression for GET /claims cache (#4592)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from coordination import claims as claims_mod
from coordination import claims_check_cache as cache_mod
from coordination_fake_rm import FakeRM, bot, claim_status, install, lease_result
from fastapi.testclient import TestClient

_BOT = {"Authorization": "Bearer bot-token", "X-Requested-With": "XMLHttpRequest"}
_CLAIM = {"repo": "Tools", "issue": 12, "agent": "codex", "session": "codex-12", "intent": "ship"}


@pytest.fixture()
def rm(tmp_path, monkeypatch) -> FakeRM:
    yield from install(tmp_path, monkeypatch, {"bot-token": bot("agent-codex")}, "Tools,GauGuru")


def _client(peer: tuple[str, int] = ("100.64.0.9", 51000)) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=peer)


@pytest.fixture()
def client(rm: FakeRM) -> TestClient:
    return _client()


def test_get_claims_includes_cache_metadata(rm: FakeRM, client: TestClient) -> None:
    rm.respond("check_agent_claim", claim_status(False))
    body = client.get("/api/coordination/claims?repo=Tools&issue=12").json()
    assert body["available"] is True
    assert body["stale"] is False
    assert "as_of" in body
    assert "generated_at" in body


def test_concurrent_get_claims_one_subprocess(rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    started = threading.Event()
    gate = threading.Event()

    def slow_check(_repo: str, _issue: int) -> dict:
        nonlocal calls
        calls += 1
        started.set()
        gate.wait(timeout=5.0)
        return {"available": True, "held": False, "agent": "", "session": "", "reason": "", "expires_at": None}

    monkeypatch.setattr(claims_mod, "check", slow_check)
    cache_mod.reset()
    monkeypatch.setenv("CLAIMS_CHECK_REQUEST_BUDGET_SECONDS", "20")

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(client.get, "/api/coordination/claims?repo=Tools&issue=12") for _ in range(6)]
        assert started.wait(timeout=2.0)
        gate.set()
        responses = [f.result(timeout=10.0) for f in as_completed(futures)]

    assert calls == 1
    assert all(r.status_code == 200 for r in responses)


def test_get_claims_503_with_retry_after(rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = threading.Event()

    def slow_check(_repo: str, _issue: int) -> dict:
        gate.wait(timeout=5.0)
        return {"available": True, "held": False, "agent": "", "session": "", "reason": "", "expires_at": None}

    monkeypatch.setattr(claims_mod, "check", slow_check)
    cache_mod.reset()
    monkeypatch.setenv("CLAIMS_CHECK_REQUEST_BUDGET_SECONDS", "0.05")

    resp = client.get("/api/coordination/claims?repo=Tools&issue=88")
    gate.set()
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After") is not None
    assert "guidance" in resp.json()["detail"]


def test_post_claim_invalidates_cached_get(rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", lease_result())

    loader_calls = 0

    def counting_loader(repo: str, issue: int) -> dict:
        nonlocal loader_calls
        loader_calls += 1
        return claims_mod.check(repo, issue)

    monkeypatch.setattr(cache_mod, "_loader", counting_loader)
    cache_mod.reset()

    client.get("/api/coordination/claims?repo=Tools&issue=12")
    client.get("/api/coordination/claims?repo=Tools&issue=12")
    assert loader_calls == 1
    client.post("/api/coordination/claims", json=_CLAIM, headers=_BOT)
    client.get("/api/coordination/claims?repo=Tools&issue=12")
    assert loader_calls == 2
