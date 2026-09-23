"""Fleet Coordination API: board reads, writes, cache and degrade paths (issue #1229, epic #1192).

The Repository_Management scripts are faked by ``coordination_fake_rm.FakeRM``:
a temp RM root whose ``scripts/*.py`` log their argv and print canned JSON.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from coordination import board as board_mod
from coordination.models import normalize_scope_path
from coordination_fake_rm import FakeRM, board, bot, claim_status, install, session
from fastapi.testclient import TestClient
from staff import runner as runner_mod
from staff import store as store_mod

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_BOT = {"Authorization": "Bearer bot-token", **_XHR}


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    principals = {"bot-token": bot("agent-codex")}
    yield from install(tmp_path, monkeypatch, principals, repos="Tools,Games")


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
    client.get("/api/coordination/sessions")  # stale: served from cache, one background refresh
    assert board_mod.join_refreshes(), "background refresh did not finish"
    assert len(_argv(rm, "agent_communicate")) == 2
    client.get("/api/coordination/sessions")  # refreshed entry is fresh again
    assert len(_argv(rm, "agent_communicate")) == 2


@pytest.mark.unit
def test_stale_board_read_is_served_immediately_while_refreshing(
    rm: FakeRM, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    monkeypatch.setattr(board_mod, "_clock", lambda: now[0])
    rm.respond("agent_communicate:list", board(session("s1", "Tools")))
    assert [s["session"] for s in client.get("/api/coordination/sessions").json()["sessions"]] == ["s1"]
    rm.respond("agent_communicate:list", board(session("s2", "Tools")))
    now[0] += 61
    stale = client.get("/api/coordination/sessions").json()["sessions"]
    assert [s["session"] for s in stale] == ["s1"]  # no wait on the slow GitHub read
    assert board_mod.join_refreshes(), "background refresh did not finish"
    assert [s["session"] for s in client.get("/api/coordination/sessions").json()["sessions"]] == ["s2"]


@pytest.mark.unit
def test_reset_cache_discards_reads_still_in_flight() -> None:
    """A refresh leaked from an earlier test must not repopulate the next test's cache (CI flake, #1243)."""
    board_mod.reset_cache()
    before = board_mod._generation
    board_mod.reset_cache()
    board_mod._store("k", 0.0, {"available": True}, before)
    assert "k" not in board_mod._cache
    board_mod.reset_cache()


@pytest.mark.unit
def test_join_refreshes_waits_for_background_threads() -> None:
    board_mod.reset_cache()
    gate = threading.Event()
    board_mod._cache["k"] = (-1e9, {"available": True, "value": "old"})

    def slow() -> dict[str, Any]:
        gate.wait(5)
        return {"available": True, "value": "new"}

    assert board_mod._cached("k", slow)["value"] == "old"
    assert not board_mod.join_refreshes(timeout=0.05)  # still running
    gate.set()
    assert board_mod.join_refreshes()
    assert board_mod._cache["k"][1]["value"] == "new"
    board_mod.reset_cache()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("./backend/coordination/", "backend/coordination"), ("docs/x.md", "docs/x.md"), (" clients/ ", "clients")],
)
def test_presence_paths_are_normalised(raw: str, expected: str) -> None:
    assert normalize_scope_path(raw) == expected


@pytest.mark.unit
@pytest.mark.parametrize("raw", ["", "/abs", "../up", "a/./b", "src/*.py", "C:/x", "-flag", "a//b"])
def test_presence_paths_rm_would_reject_are_422(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_scope_path(raw)


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
        "agent": "codex", "session": "codex-42", "repo": "Tools", "issue": 42, "branch": "feat/x",
        "paths": ["a.py", "b.py"], "goals": {"api": "ship v1"}, "ttl_hours": 3,
    }  # fmt: skip
    resp = client.post("/api/coordination/presence", json=body, headers=_BOT)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    argv = [a for a in _argv(rm, "agent_communicate") if "register" in a][0]
    assert argv[:4] == ["--repo", "Tools", "--session", "codex-42"]
    tail = argv[argv.index("register") :]
    assert tail == [
        "register", "--agent", "codex", "--issue", "42", "--branch", "feat/x",
        "--path", "a.py", "--path", "b.py", "--goal", "api=ship v1", "--ttl-hours", "3.0",
    ]  # fmt: skip
    client.get("/api/coordination/sessions")
    assert len([a for a in _argv(rm, "agent_communicate") if "list" in a]) == 2


@pytest.mark.unit
def test_owner_prefixed_repo_is_normalised_to_the_bare_name(rm: FakeRM, client: TestClient) -> None:
    rm.respond("check_agent_claim", claim_status(False))
    assert client.get("/api/coordination/claims?repo=D-sorganization/Tools&issue=3").json()["available"] is True
    rm.respond("agent_communicate:release", {"ok": True})
    rel = {"session": "codex-1", "repo": "D-sorganization/Tools"}
    assert client.post("/api/coordination/presence/release", json=rel, headers=_BOT).status_code == 200
    assert [argv[1] for _, argv in rm.calls()] == ["Tools", "Tools"]
    assert client.get("/api/coordination/claims?repo=a..b&issue=3").status_code == 422


@pytest.mark.unit
def test_agent_defaults_to_bot_principal(rm: FakeRM, client: TestClient) -> None:
    rm.respond("agent_communicate:register", {"ok": True})
    body = {"session": "codex-1", "repo": "Tools", "issue": 1, "branch": "b"}
    assert client.post("/api/coordination/presence", json=body, headers=_BOT).status_code == 200
    argv = _argv(rm, "agent_communicate")[0]
    assert argv[argv.index("--agent") + 1] == "codex"  # bot principal agent-codex


@pytest.mark.unit
def test_write_failure_returns_502_with_error_and_guidance(rm: FakeRM, client: TestClient) -> None:
    rm.respond("agent_communicate:list", board(session("codex-1", "Tools", agent="codex")))
    rm.respond("agent_communicate:send", {"ok": False, "error": "board locked", "guidance": "retry later"}, rc=2)
    body = {"session": "codex-1", "repo": "Tools", "to": "*", "text": "heads up"}
    resp = client.post("/api/coordination/messages", json=body, headers=_BOT)
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["error"] == "board locked" and detail["guidance"] == "retry later"


@pytest.mark.unit
def test_send_ack_and_release_call_the_matching_subcommands(rm: FakeRM, client: TestClient) -> None:
    rm.respond("agent_communicate:list", board(session("codex-1", "Tools", agent="codex")))
    for cmd in ("send", "ack", "release"):
        rm.respond(f"agent_communicate:{cmd}", {"ok": True})
    msg = {"session": "codex-1", "repo": "Tools", "to": "claude-2", "text": "rebasing"}
    assert client.post("/api/coordination/messages", json=msg, headers=_BOT).status_code == 200
    ack = {"session": "codex-1", "repo": "Tools", "message_id": "evt-9"}
    assert client.post("/api/coordination/messages/ack", json=ack, headers=_BOT).status_code == 200
    rel = {"session": "codex-1", "repo": "Tools"}
    assert client.post("/api/coordination/presence/release", json=rel, headers=_BOT).status_code == 200
    tails = [a[4:] for a in _argv(rm, "agent_communicate") if "list" not in a]
    assert tails == [["send", "--to", "claude-2", "--text=rebasing"], ["ack", "evt-9"], ["release"]]


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
