"""Fleet Coordination API: claims, briefing and the write-auth perimeter (issue #1229, epic #1192)."""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from pathlib import Path

import identity
import pytest
from coordination import board as board_mod
from coordination_fake_rm import FakeRM, board, session
from fastapi.testclient import TestClient
from staff import fleet as staff_fleet
from staff import runner as runner_mod
from staff import scheduler as scheduler_mod
from staff import store as store_mod
from staff.workspace import FLEET_RULES

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_BOT = {"Authorization": "Bearer bot-token", **_XHR}
_VIEWER = {"Authorization": "Bearer viewer-token", **_XHR}
_REMOTE = ("100.64.0.9", 51000)
_CLAIM = {"repo": "Tools", "issue": 12, "agent": "codex", "session": "s-12", "intent": "implement"}


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    fake = FakeRM(tmp_path / "rm")
    monkeypatch.setenv("STAFF_RM_ROOT", str(fake.root))
    monkeypatch.setenv("STAFF_RM_PYTHON", sys.executable)
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "no-roles"))
    monkeypatch.setenv("STAFF_HOLDS_FILE", str(tmp_path / "holds.json"))
    monkeypatch.setenv("COORDINATION_REPOS", "Tools")
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)
    monkeypatch.setattr(staff_fleet, "peer_nodes", lambda: {})
    principals = {
        "bot-token": identity.Principal(id="claude-code", type="bot", name="Claude Code", roles=["bot"]),
        "viewer-token": identity.Principal(id="watcher", type="human", name="Watcher", roles=["viewer"]),
    }
    monkeypatch.setattr(identity.identity_manager, "verify_token", lambda raw: principals.get(raw))
    store_mod.reset_store()
    runner_mod.reset_runner()
    scheduler_mod.reset_scheduler()
    board_mod.reset_cache()
    yield fake
    board_mod.reset_cache()
    scheduler_mod.reset_scheduler()
    runner_mod.reset_runner()
    store_mod.reset_store()


def _client(peer: tuple[str, int] = _REMOTE) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=peer)


@pytest.fixture
def client(rm: FakeRM) -> TestClient:
    return _client()


def _scripts(rm: FakeRM) -> list[str]:
    return [name for name, _ in rm.calls()]


# ── claims ───────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_claim_check_returns_rm_json(rm: FakeRM, client: TestClient) -> None:
    held = {"held": True, "agent": "gemini", "reason": "lease", "expires_at": "2026-09-22T12:00"}
    rm.respond("check_agent_claim", held)
    body = client.get("/api/coordination/claims?repo=Tools&issue=12").json()
    assert body["available"] is True and body["held"] is True and body["agent"] == "gemini"
    assert rm.calls()[0][1] == ["--repo", "Tools", "--issue", "12"]


@pytest.mark.unit
def test_claim_held_by_another_agent_is_409_and_posts_nothing(rm: FakeRM, client: TestClient) -> None:
    rm.respond("check_agent_claim", {"held": True, "agent": "gemini", "reason": "lease", "expires_at": None})
    resp = client.post("/api/coordination/claims", json=_CLAIM, headers=_BOT)
    assert resp.status_code == 409
    assert resp.json()["detail"]["held_by"] == "gemini"
    assert _scripts(rm) == ["check_agent_claim"]


@pytest.mark.unit
@pytest.mark.parametrize("holder", ["", "codex"])
def test_free_or_own_claim_posts_lease(rm: FakeRM, client: TestClient, holder: str) -> None:
    rm.respond("check_agent_claim", {"held": bool(holder), "agent": holder, "reason": "", "expires_at": None})
    rm.respond("post_agent_lease", {"ok": True, "agent": "codex", "expires_at": "2026-09-22T12:00:00+00:00"})
    resp = client.post("/api/coordination/claims", json=_CLAIM, headers=_BOT)
    assert resp.status_code == 200, resp.text
    assert resp.json()["lease"]["ok"] is True
    post = [argv for name, argv in rm.calls() if name == "post_agent_lease"][0]
    assert post == [
        *("--repo", "Tools", "--issue", "12"),
        *("--agent", "codex", "--session", "s-12", "--intent", "implement"),
    ]


@pytest.mark.unit
def test_unknown_agent_lease_without_json_is_502(rm: FakeRM, client: TestClient) -> None:
    rm.respond("check_agent_claim", {"held": False, "agent": "", "reason": "", "expires_at": None})
    rm.respond("post_agent_lease", None, stderr="ERROR: unknown agent")
    resp = client.post("/api/coordination/claims", json=_CLAIM, headers=_BOT)
    assert resp.status_code == 502
    assert "unknown agent" in resp.json()["detail"]["error"]


@pytest.mark.unit
def test_claim_release(rm: FakeRM, client: TestClient) -> None:
    rm.respond("release_agent_lease", {"ok": True, "removed_label": True})
    body = {**_CLAIM, "reason": "handed off"}
    body.pop("intent")
    resp = client.post("/api/coordination/claims/release", json=body, headers=_BOT)
    assert resp.status_code == 200, resp.text
    argv = rm.calls()[0][1]
    assert argv[argv.index("--reason") + 1] == "handed off"


# ── auth perimeter ───────────────────────────────────────────────────────
@pytest.mark.unit
def test_scope_presets_grant_coordination_write_to_bot_and_operator() -> None:
    assert "coordination.write" in identity.SCOPE_PRESETS["bot"]
    assert "coordination.write" in identity.SCOPE_PRESETS["operator"]
    assert "coordination.write" not in identity.SCOPE_PRESETS["viewer"]


@pytest.mark.unit
def test_anonymous_remote_write_is_401(rm: FakeRM, client: TestClient) -> None:
    assert client.post("/api/coordination/claims", json=_CLAIM, headers=_XHR).status_code == 401
    assert rm.calls() == []


@pytest.mark.unit
def test_principal_without_scope_is_403(rm: FakeRM, client: TestClient) -> None:
    assert client.post("/api/coordination/claims", json=_CLAIM, headers=_VIEWER).status_code == 403
    assert rm.calls() == []


@pytest.mark.unit
def test_write_without_csrf_header_is_403_even_with_bearer(rm: FakeRM, client: TestClient) -> None:
    headers = {"Authorization": "Bearer bot-token"}
    assert client.post("/api/coordination/claims", json=_CLAIM, headers=headers).status_code == 403
    assert rm.calls() == []


@pytest.mark.unit
def test_loopback_orchestrator_peer_may_write(rm: FakeRM, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "1")
    rm.respond("agent_communicate:release", {"ok": True})
    resp = _client(("127.0.0.1", 50000)).post(
        "/api/coordination/presence/release", json={"session": "s-1", "repo": "Tools"}, headers=_XHR
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.unit
def test_fleet_token_is_not_a_write_credential(rm: FakeRM, client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-secret")
    headers = {"Authorization": "Bearer fleet-secret", **_XHR}
    assert client.post("/api/coordination/claims", json=_CLAIM, headers=headers).status_code == 401


@pytest.mark.unit
def test_reads_need_fleet_token_once_configured(rm: FakeRM, client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HUB_FLEET_TOKEN", "fleet-secret")
    rm.respond("agent_communicate:list", board())
    assert client.get("/api/coordination/sessions").status_code == 401
    ok = client.get("/api/coordination/sessions", headers={"Authorization": "Bearer fleet-secret"})
    assert ok.status_code == 200 and ok.json()["available"] is True


# ── briefing ─────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_briefing_without_priorities_module(rm: FakeRM, client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setitem(sys.modules, "priorities.service", None)
    rm.respond("agent_communicate:list", board(session("s1", "Tools"), session("s2", "Games")))
    scheduler_mod.get_scheduler().holds.replace(
        [{"text": "no bulk stale-queue cancel"}, {"text": "lifted", "active": False}]
    )
    body = client.get("/api/coordination/briefing?repo=Tools&agent=codex").json()
    assert body["repo"] == "Tools" and body["agent"] == "codex"
    assert body["priorities"] == []
    assert [h["text"] for h in body["holds"]] == ["no bulk stale-queue cancel"]
    assert [s["session"] for s in body["sessions"]] == ["s1"]
    assert body["staff_runs"] == []
    assert body["rules"] == [FLEET_RULES]
    assert "claims" in body["endpoints"] and "/api/coordination/claims" in body["claims_hint"]
    assert "generated_at" in body


@pytest.mark.unit
def test_briefing_uses_priorities_service_when_present(rm: FakeRM, client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    fake = types.ModuleType("priorities.service")
    seen: list[int] = []

    def top_priorities(limit: int) -> list[dict]:
        seen.append(limit)
        return [{"rank": 1, "item": "ship coordination API"}]

    fake.top_priorities = top_priorities  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "priorities", types.ModuleType("priorities"))
    monkeypatch.setitem(sys.modules, "priorities.service", fake)
    rm.respond("agent_communicate:list", board())
    body = client.get("/api/coordination/briefing?repo=Tools").json()
    assert body["priorities"] == [{"rank": 1, "item": "ship coordination API"}]
    assert seen == [5]


@pytest.mark.unit
def test_briefing_survives_a_broken_priorities_service(rm: FakeRM, client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    fake = types.ModuleType("priorities.service")

    def top_priorities(limit: int) -> list[dict]:
        raise RuntimeError("consensus.md unreadable")

    fake.top_priorities = top_priorities  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "priorities", types.ModuleType("priorities"))
    monkeypatch.setitem(sys.modules, "priorities.service", fake)
    rm.respond("agent_communicate:list", board())
    resp = client.get("/api/coordination/briefing?repo=Tools")
    assert resp.status_code == 200
    assert resp.json()["priorities"] == []
    assert any("priorities" in w for w in resp.json()["warnings"])


@pytest.mark.unit
def test_write_without_agent_needs_a_bot_principal(rm: FakeRM, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "1")
    body = {"repo": "Tools", "issue": 12, "session": "s-12"}
    resp = _client(("127.0.0.1", 50000)).post("/api/coordination/claims", json=body, headers=_XHR)
    assert resp.status_code == 422
    assert "agent is required" in resp.json()["detail"]
    assert rm.calls() == []
