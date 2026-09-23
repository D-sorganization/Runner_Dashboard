"""Fleet Coordination API: board reads, writes, cache and degrade paths (issue #1229, epic #1192).

The Repository_Management scripts are faked by ``coordination_fake_rm.FakeRM``:
a temp RM root whose ``scripts/*.py`` log their argv and print canned JSON.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import identity
import pytest
from coordination import board as board_mod
from coordination_fake_rm import FakeRM, board, session
from fastapi.testclient import TestClient
from staff import fleet as staff_fleet
from staff import runner as runner_mod
from staff import scheduler as scheduler_mod
from staff import store as store_mod

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_BOT = {"Authorization": "Bearer bot-token", **_XHR}


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    fake = FakeRM(tmp_path / "rm")
    monkeypatch.setenv("STAFF_RM_ROOT", str(fake.root))
    monkeypatch.setenv("STAFF_RM_PYTHON", sys.executable)
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "no-roles"))
    monkeypatch.setenv("STAFF_HOLDS_FILE", str(tmp_path / "holds.json"))
    monkeypatch.setenv("COORDINATION_REPOS", "Tools,Games")
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)
    monkeypatch.setattr(staff_fleet, "peer_nodes", lambda: {})
    bot = identity.Principal(id="claude-code", type="bot", name="Claude Code", roles=["bot"])
    monkeypatch.setattr(identity.identity_manager, "verify_token", lambda raw: bot if raw == "bot-token" else None)
    store_mod.reset_store()
    runner_mod.reset_runner()
    scheduler_mod.reset_scheduler()
    board_mod.reset_cache()
    yield fake
    board_mod.reset_cache()
    scheduler_mod.reset_scheduler()
    runner_mod.reset_runner()
    store_mod.reset_store()


@pytest.fixture
def client(rm: FakeRM) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=("100.64.0.9", 51000))


def _argv(rm: FakeRM, script: str) -> list[list[str]]:
    return [argv for name, argv in rm.calls() if name == script]


# ── reads ────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_sessions_reads_board_once_with_all_repos_and_filters(rm: FakeRM, client: TestClient) -> None:
    rm.respond("agent_communicate:list", board(session("s1", "Tools"), session("s2", "Games")))
    body = client.get("/api/coordination/sessions?repo=tools").json()
    assert body["available"] is True and body["complete"] is True
    assert [s["session"] for s in body["sessions"]] == ["s1"]
    s1 = body["sessions"][0]
    assert s1["source"] == "board" and s1["issue"] == 7 and s1["goals"] == {"api": "ship"}
    assert body["staff_runs"] == [] and "generated_at" in body
    assert len(_argv(rm, "agent_communicate")) == 1
    assert "--all-repos" in _argv(rm, "agent_communicate")[0]
    everything = client.get("/api/coordination/sessions").json()
    assert {s["session"] for s in everything["sessions"]} == {"s1", "s2"}


@pytest.mark.unit
def test_sessions_fall_back_to_per_repo_list_without_all_repos(rm: FakeRM, client: TestClient) -> None:
    rm.disable_all_repos()
    rm.respond("agent_communicate:list", board(session("s1", "Tools")))
    body = client.get("/api/coordination/sessions").json()
    assert body["available"] is True
    per_repo = [a for a in _argv(rm, "agent_communicate") if "--all-repos" not in a]
    repos = sorted(a[a.index("--repo") + 1] for a in per_repo)
    assert repos == ["Games", "Repository_Management", "Tools"]
    assert [s["session"] for s in body["sessions"]] == ["s1"]  # de-duplicated across calls


@pytest.mark.unit
def test_board_reads_are_cached_for_60_seconds(rm: FakeRM, client: TestClient, monkeypatch) -> None:  # noqa: ANN001
    now = [1000.0]
    monkeypatch.setattr(board_mod, "_clock", lambda: now[0])
    rm.respond("agent_communicate:list", board(session("s1", "Tools")))
    client.get("/api/coordination/sessions")
    client.get("/api/coordination/sessions?repo=Tools")
    assert len(_argv(rm, "agent_communicate")) == 1
    now[0] += 61
    client.get("/api/coordination/sessions")
    assert len(_argv(rm, "agent_communicate")) == 2


@pytest.mark.unit
def test_sessions_include_staff_runs_in_flight(rm: FakeRM, client: TestClient) -> None:
    rm.respond("agent_communicate:list", board())
    store = runner_mod.get_runner().store
    store.create_run(
        store_mod.RunRecord(
            id="run-1", role="night-watch", provider="claude", model=None, machine="Desk", repo="Tools",
            target_kind="issue", target_ref="#5", prompt="", status="running", started_at="2026-09-22T10:00:00Z",
        )
    )  # fmt: skip
    runs = client.get("/api/coordination/sessions?repo=Tools").json()["staff_runs"]
    assert runs == [
        {
            "id": "run-1", "role": "night-watch", "provider": "claude", "machine": "Desk", "repo": "Tools",
            "target": "#5", "status": "running", "started_at": "2026-09-22T10:00:00Z", "source": "staff",
        }
    ]  # fmt: skip
    assert client.get("/api/coordination/sessions?repo=Games").json()["staff_runs"] == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stdout", "rc"),
    [
        ({"ok": False, "error": "gh: not logged in", "guidance": "retain leases"}, 2),
        ("Traceback (most recent call last): boom", 1),
    ],
)
def test_board_failure_degrades_to_unavailable(rm: FakeRM, client: TestClient, stdout: Any, rc: int) -> None:
    rm.respond("agent_communicate:list", stdout, rc=rc)
    resp = client.get("/api/coordination/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False and body["reason"]
    assert body["sessions"] == []


@pytest.mark.unit
def test_missing_rm_checkout_degrades(rm: FakeRM, client: TestClient, monkeypatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setenv("STAFF_RM_ROOT", str(tmp_path / "nowhere"))
    body = client.get("/api/coordination/inbox?session=s1").json()
    assert body["available"] is False and "Repository_Management" in body["reason"]


@pytest.mark.unit
def test_incomplete_board_is_available_with_warnings(rm: FakeRM, client: TestClient) -> None:
    rm.respond("agent_communicate:list", board(session("s1", "Tools"), complete=False, warnings=["page 3 failed"]))
    body = client.get("/api/coordination/sessions").json()
    assert body["available"] is True and body["complete"] is False
    assert body["warnings"] == ["page 3 failed"]


@pytest.mark.unit
def test_inbox_passes_session_and_returns_messages(rm: FakeRM, client: TestClient) -> None:
    payload = {**board(), "messages": [{"id": "m1", "text": "hi"}], "conflicts": [{"paths": ["a"]}]}
    rm.respond("agent_communicate:inbox", payload)
    body = client.get("/api/coordination/inbox?session=s1&repo=Tools").json()
    assert body["available"] is True
    assert body["messages"] == [{"id": "m1", "text": "hi"}] and body["conflicts"] == [{"paths": ["a"]}]
    argv = _argv(rm, "agent_communicate")[0]
    assert argv[argv.index("--session") + 1] == "s1" and argv[argv.index("--repo") + 1] == "Tools"


# ── writes ───────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_presence_register_passes_every_field_and_invalidates_cache(rm: FakeRM, client: TestClient) -> None:
    rm.respond("agent_communicate:list", board())
    rm.respond("agent_communicate:register", {"ok": True, "receipt": "c-1", "event": {"id": "e1"}})
    client.get("/api/coordination/sessions")
    body = {
        "agent": "codex", "session": "s-42", "repo": "Tools", "issue": 42, "branch": "feat/x",
        "paths": ["a.py", "b.py"], "goals": {"api": "ship v1"}, "ttl_hours": 3,
    }  # fmt: skip
    resp = client.post("/api/coordination/presence", json=body, headers=_BOT)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    argv = [a for a in _argv(rm, "agent_communicate") if "register" in a][0]
    assert argv[:4] == ["--repo", "Tools", "--session", "s-42"]
    tail = argv[argv.index("register") :]
    assert tail == [
        "register", "--agent", "codex", "--issue", "42", "--branch", "feat/x",
        "--path", "a.py", "--path", "b.py", "--goal", "api=ship v1", "--ttl-hours", "3.0",
    ]  # fmt: skip
    client.get("/api/coordination/sessions")
    assert len([a for a in _argv(rm, "agent_communicate") if "list" in a]) == 2


@pytest.mark.unit
def test_owner_prefixed_repo_is_normalised_to_the_bare_name(rm: FakeRM, client: TestClient) -> None:
    rm.respond("check_agent_claim", {"held": False, "agent": "", "reason": "", "expires_at": None})
    assert client.get("/api/coordination/claims?repo=D-sorganization/Tools&issue=3").json()["available"] is True
    rm.respond("agent_communicate:release", {"ok": True})
    rel = {"session": "s-1", "repo": "D-sorganization/Tools"}
    assert client.post("/api/coordination/presence/release", json=rel, headers=_BOT).status_code == 200
    assert [argv[1] for _, argv in rm.calls()] == ["Tools", "Tools"]
    assert client.get("/api/coordination/claims?repo=a..b&issue=3").status_code == 422


@pytest.mark.unit
def test_agent_defaults_to_bot_principal(rm: FakeRM, client: TestClient) -> None:
    rm.respond("agent_communicate:register", {"ok": True})
    body = {"session": "s-1", "repo": "Tools", "issue": 1, "branch": "b"}
    assert client.post("/api/coordination/presence", json=body, headers=_BOT).status_code == 200
    argv = _argv(rm, "agent_communicate")[0]
    assert argv[argv.index("--agent") + 1] == "claude-code"


@pytest.mark.unit
def test_write_failure_returns_502_with_error_and_guidance(rm: FakeRM, client: TestClient) -> None:
    rm.respond("agent_communicate:send", {"ok": False, "error": "board locked", "guidance": "retry later"}, rc=2)
    body = {"session": "s-1", "repo": "Tools", "to": "*", "text": "heads up"}
    resp = client.post("/api/coordination/messages", json=body, headers=_BOT)
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["error"] == "board locked" and detail["guidance"] == "retry later"


@pytest.mark.unit
def test_send_ack_and_release_call_the_matching_subcommands(rm: FakeRM, client: TestClient) -> None:
    for cmd in ("send", "ack", "release"):
        rm.respond(f"agent_communicate:{cmd}", {"ok": True})
    msg = {"session": "s-1", "repo": "Tools", "to": "s-2", "text": "rebasing"}
    assert client.post("/api/coordination/messages", json=msg, headers=_BOT).status_code == 200
    ack = {"session": "s-1", "repo": "Tools", "message_id": "evt-9"}
    assert client.post("/api/coordination/messages/ack", json=ack, headers=_BOT).status_code == 200
    rel = {"session": "s-1", "repo": "Tools"}
    assert client.post("/api/coordination/presence/release", json=rel, headers=_BOT).status_code == 200
    tails = [a[4:] for a in _argv(rm, "agent_communicate")]
    assert tails == [["send", "--to", "s-2", "--text=rebasing"], ["ack", "evt-9"], ["release"]]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/coordination/messages", {"session": "s", "repo": "Tools", "to": "*", "text": "x" * 4001}),
        ("/api/coordination/messages", {"session": "has space", "repo": "Tools", "to": "*", "text": "x"}),
        ("/api/coordination/presence", {"session": "s", "repo": "Tools", "issue": 1, "branch": "b", "ttl_hours": 9}),
        ("/api/coordination/presence", {"session": "s", "repo": "Tools", "issue": 1, "branch": "b", "ttl_hours": 0}),
        ("/api/coordination/presence", {"session": "s", "repo": "../etc", "issue": 1, "branch": "b"}),
        ("/api/coordination/claims", {"session": "s", "repo": "Tools", "issue": 0}),
        ("/api/coordination/claims", {"session": "s", "repo": "a/b/Tools", "issue": 3}),
        ("/api/coordination/claims", {"session": "s", "repo": "Tools", "issue": 3, "intent": "-x"}),
    ],
)
def test_request_contracts_reject_bad_input(rm: FakeRM, client: TestClient, path: str, body: dict) -> None:
    assert client.post(path, json=body, headers=_BOT).status_code == 422
    assert rm.calls() == []
