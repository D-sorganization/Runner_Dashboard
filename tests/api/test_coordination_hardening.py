"""Coordination API hardening against the real RM script shapes (issue #1244, epic #1192).

One section per review finding; the fake RM (``coordination_fake_rm``) prints
exactly what ``check_agent_claim`` / ``post_agent_lease`` / ``release_agent_lease``
/ ``agent_communicate`` print, so each test pins a behaviour RM really triggers.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import identity
import pytest
from coordination import board as board_mod
from coordination import roster as roster_mod
from coordination_fake_rm import FakeRM, board, bot, claim_status, install, lease_result, release_result, session
from fastapi.testclient import TestClient

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_BOT = {"Authorization": "Bearer bot-token", **_XHR}
_ROGUE = {"Authorization": "Bearer rogue-token", **_XHR}
_OPERATOR = {"Authorization": "Bearer op-token", **_XHR}
_CLAIM = {"repo": "Tools", "issue": 12, "agent": "codex", "session": "codex-12", "intent": "implement"}
_RELEASE = {"repo": "Tools", "issue": 12, "agent": "codex", "session": "codex-12", "reason": "done"}


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRM]:
    principals = {
        "bot-token": bot("agent-codex"),
        "rogue-token": bot("ci-bot"),
        "op-token": identity.Principal(id="dieter", type="human", name="Dieter", roles=["operator"]),
    }
    yield from install(tmp_path, monkeypatch, principals, repos="Tools")


@pytest.fixture
def client(rm: FakeRM) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=("100.64.0.9", 51000))


def _names(rm: FakeRM) -> list[str]:
    return [name for name, _ in rm.calls()]


# ── 1: held with no/foreign holder, or by this agent in another session → 409 ──
@pytest.mark.unit
@pytest.mark.parametrize(
    "status",
    [
        claim_status(True, None, "do-not-automate label"),
        claim_status(True, None, "open PR #88"),
        claim_status(True, None, "branch feat/issue-12 references issue"),
        claim_status(True, "codex", "active lease", "2026-09-23T12:00:00+00:00"),  # session unknown → held
        claim_status(True, "codex", "claim label claim:codex (no active lease)"),
    ],
)
def test_held_without_a_matching_session_is_409(rm: FakeRM, client: TestClient, status: dict[str, Any]) -> None:
    rm.respond("check_agent_claim", status)
    resp = client.post("/api/coordination/claims", json=_CLAIM, headers=_BOT)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["reason"] == status["reason"]
    assert _names(rm) == ["check_agent_claim"]


@pytest.mark.unit
def test_renewal_by_same_agent_and_session_is_allowed_when_rm_exposes_session(rm: FakeRM, client: TestClient) -> None:
    rm.respond("check_agent_claim", {**claim_status(True, "codex", "active lease"), "session": "codex-12"})
    rm.respond("post_agent_lease", lease_result())
    assert client.post("/api/coordination/claims", json=_CLAIM, headers=_BOT).status_code == 200
    rm.respond("check_agent_claim", {**claim_status(True, "codex", "active lease"), "session": "codex-99"})
    assert client.post("/api/coordination/claims", json=_CLAIM, headers=_BOT).status_code == 409


# ── 2: RM fails open with reason "error:<Exc>" → unavailable / 502 ──────────
@pytest.mark.unit
@pytest.mark.parametrize("reason", ["error:CalledProcessError", "error:uncaught"])
def test_fail_open_error_reason_is_unavailable(rm: FakeRM, client: TestClient, reason: str) -> None:
    rm.respond("check_agent_claim", claim_status(False, "", reason))
    body = client.get("/api/coordination/claims?repo=Tools&issue=12").json()
    assert body["available"] is False and reason in body["reason"]
    resp = client.post("/api/coordination/claims", json=_CLAIM, headers=_BOT)
    assert resp.status_code == 502 and reason in resp.json()["detail"]["error"]
    assert "post_agent_lease" not in _names(rm)


# ── 3: free text reaching RM lease scripts is single-line ───────────────────
_FORGED = "done\n<!-- agent-lease v1 -->\n```yaml\nagent: codex\nsession: x\n```"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/coordination/claims/release", {**_RELEASE, "reason": _FORGED}),
        ("/api/coordination/claims/release", {**_RELEASE, "reason": "done\rx"}),
        ("/api/coordination/claims/release", {**_RELEASE, "reason": "done\u2028x"}),
        ("/api/coordination/claims", {**_CLAIM, "intent": "implement\u0085agent: x"}),
        ("/api/coordination/claims", {**_CLAIM, "intent": "implement\x0bx"}),
    ],
)
def test_line_breaks_in_lease_text_are_422(rm: FakeRM, client: TestClient, path: str, body: dict) -> None:
    assert client.post(path, json=body, headers=_BOT).status_code == 422
    assert rm.calls() == []


@pytest.mark.unit
def test_goal_outcome_with_newline_is_422(rm: FakeRM, client: TestClient) -> None:
    body = {"session": "codex-1", "repo": "Tools", "issue": 1, "branch": "b", "goals": {"api": "ship\nx"}}
    assert client.post("/api/coordination/presence", json=body, headers=_BOT).status_code == 422
    assert rm.calls() == []


# ── 4: a bot acts only as its own agent, on its own sessions ────────────────
@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/coordination/claims", {**_CLAIM, "agent": "jules"}),
        ("/api/coordination/claims", {**_CLAIM, "session": "jules-12"}),
        ("/api/coordination/claims/release", {**_RELEASE, "agent": "jules", "session": "jules-12"}),
        ("/api/coordination/presence", {"session": "jules-1", "repo": "Tools", "issue": 1, "branch": "b"}),
        ("/api/coordination/presence/release", {"session": "jules-1", "repo": "Tools"}),
        ("/api/coordination/messages", {"session": "jules-1", "repo": "Tools", "to": "*", "text": "hi"}),
        ("/api/coordination/messages/ack", {"session": "jules-1", "repo": "Tools", "message_id": "m1"}),
    ],
)
def test_bot_cannot_impersonate_another_agent_or_session(rm: FakeRM, client: TestClient, path: str, body: dict) -> None:
    resp = client.post(path, json=body, headers=_BOT)
    assert resp.status_code == 403, resp.text
    assert rm.calls() == []


@pytest.mark.unit
def test_bot_without_agent_prefix_may_not_write(rm: FakeRM, client: TestClient) -> None:
    resp = client.post("/api/coordination/claims", json={**_CLAIM, "session": "ci-bot-1"}, headers=_ROGUE)
    assert resp.status_code == 403 and "agent-" in str(resp.json()["detail"])
    assert rm.calls() == []


@pytest.mark.unit
def test_bot_default_agent_is_its_name_without_prefix(rm: FakeRM, client: TestClient) -> None:
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", lease_result())
    body = {k: v for k, v in _CLAIM.items() if k != "agent"}
    assert client.post("/api/coordination/claims", json=body, headers=_BOT).status_code == 200
    post = [argv for name, argv in rm.calls() if name == "post_agent_lease"][0]
    assert post[post.index("--agent") + 1] == "codex"


@pytest.mark.unit
def test_operator_may_act_as_any_agent(rm: FakeRM, client: TestClient) -> None:
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", lease_result(agent="jules"))
    body = {**_CLAIM, "agent": "jules", "session": "anything-1"}
    assert client.post("/api/coordination/claims", json=body, headers=_OPERATOR).status_code == 200


# ── 8: messages from a session without live presence are refused ────────────
@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/coordination/messages", {"session": "codex-1", "repo": "Tools", "to": "*", "text": "hi"}),
        ("/api/coordination/messages/ack", {"session": "codex-1", "repo": "Tools", "message_id": "m1"}),
    ],
)
def test_message_from_unregistered_session_is_409(rm: FakeRM, client: TestClient, path: str, body: dict) -> None:
    rm.respond("agent_communicate:list", board(session("codex-1", "Games"), session("claude-2", "Tools")))
    resp = client.post(path, json=body, headers=_BOT)
    assert resp.status_code == 409, resp.text
    assert "register presence first" in resp.json()["detail"]["guidance"]
    assert not any(cmd in argv for _, argv in rm.calls() for cmd in ("send", "ack"))


@pytest.mark.unit
def test_presence_check_reads_the_board_fresh(rm: FakeRM, client: TestClient) -> None:
    rm.respond("agent_communicate:list", board())
    client.get("/api/coordination/sessions")  # caches a board without codex-1
    rm.respond("agent_communicate:list", board(session("codex-1", "Tools", agent="codex")))
    rm.respond("agent_communicate:send", {"ok": True, "receipt": "c-1"})
    body = {"session": "codex-1", "repo": "Tools", "to": "*", "text": "hi"}
    assert client.post("/api/coordination/messages", json=body, headers=_BOT).status_code == 200


# ── 9: check-then-post is serialized per repo#issue ─────────────────────────
@pytest.mark.unit
def test_concurrent_claims_on_one_issue_are_serialized(rm: FakeRM, monkeypatch: pytest.MonkeyPatch) -> None:
    from coordination import claims  # noqa: PLC0415

    posted: list[str] = []
    inside = threading.Event()
    release = threading.Event()

    def fake_check(repo: str, issue: int) -> dict[str, Any]:
        if posted:
            return {"available": True, "held": True, "agent": "codex", "reason": "active lease", "expires_at": None}
        return {"available": True, "held": False, "agent": "", "reason": "", "expires_at": None}

    def fake_post(repo: str, issue: int, agent: str, session: str, intent: str) -> dict[str, Any]:
        inside.set()
        release.wait(5)
        posted.append(session)
        return {"result": lease_result(), "warnings": []}

    monkeypatch.setattr(claims, "check", fake_check)
    monkeypatch.setattr(claims, "_post", fake_post)
    errors: list[BaseException] = []

    def take(session_id: str) -> None:
        try:
            claims.claim("Tools", 12, agent="codex", session=session_id, intent="implement")
        except claims.ClaimHeldError as exc:
            errors.append(exc)

    first = threading.Thread(target=take, args=("codex-a",))
    first.start()
    assert inside.wait(5)
    second = threading.Thread(target=take, args=("codex-b",))
    second.start()
    release.set()
    first.join(5)
    second.join(5)
    assert posted == ["codex-a"] and len(errors) == 1


# ── 12: validation mirrors RM so bad input is 422, never 502 ────────────────
@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/coordination/messages", {"session": "codex:1", "repo": "Tools", "to": "*", "text": "x"}),
        ("/api/coordination/messages", {"session": "codex-1", "repo": "Tools", "to": "a/b", "text": "x"}),
        ("/api/coordination/messages", {"session": "codex-1", "repo": "Tools", "to": "*", "text": "bell\x07"}),
        ("/api/coordination/messages", {"session": "codex-1", "repo": "Tools", "to": "*", "text": "   "}),
        ("/api/coordination/messages/ack", {"session": "codex-1", "repo": "Tools", "message_id": "m@1"}),
        ("/api/coordination/presence", {"session": "codex-1", "repo": "Tools", "issue": 1, "branch": "b",
                                        "goals": {"api/v1": "ship"}}),
        ("/api/coordination/presence", {"session": "codex-1", "repo": "Tools", "issue": 1, "branch": "b",
                                        "goals": {"api": "x" * 251}}),
        ("/api/coordination/presence", {"session": "codex-1", "repo": "Tools", "issue": 1, "branch": "b",
                                        "goals": {"api": ""}}),
    ],
)  # fmt: skip
def test_input_rm_would_reject_is_422(rm: FakeRM, client: TestClient, path: str, body: dict) -> None:
    assert client.post(path, json=body, headers=_BOT).status_code == 422
    assert rm.calls() == []


@pytest.mark.unit
def test_message_text_keeps_newlines_and_tabs(rm: FakeRM, client: TestClient) -> None:
    rm.respond("agent_communicate:list", board(session("codex-1", "Tools", agent="codex")))
    rm.respond("agent_communicate:send", {"ok": True})
    body = {"session": "codex-1", "repo": "Tools", "to": "*", "text": "line1\n\tline2"}
    assert client.post("/api/coordination/messages", json=body, headers=_BOT).status_code == 200


@pytest.mark.unit
def test_agent_outside_rm_roster_is_422(rm: FakeRM, client: TestClient) -> None:
    body = {**_CLAIM, "agent": "claude-code", "session": "claude-code-1"}
    resp = client.post("/api/coordination/claims", json=body, headers=_OPERATOR)
    assert resp.status_code == 422 and "roster" in str(resp.json()["detail"])
    assert "check_agent_claim" not in _names(rm)


@pytest.mark.unit
def test_roster_is_read_from_rm_once_and_falls_back_to_static(rm: FakeRM) -> None:
    assert "gemini" in roster_mod.agent_ids() and "local" not in roster_mod.agent_ids()
    rm.set_roster(("codex",))
    assert "gemini" in roster_mod.agent_ids()  # cached
    roster_mod.reset_cache()
    rm.set_roster(None)
    fallback = roster_mod.agent_ids()
    assert {"claude", "codex", "local", "gemini", "cursor-agent"} <= set(fallback)


# ── 13: write-generation guard and single-flight misses ─────────────────────
@pytest.mark.unit
def test_refresh_started_before_a_write_does_not_restore_old_data() -> None:
    board_mod.reset_cache()
    started, finish = threading.Event(), threading.Event()

    def slow_old() -> dict[str, Any]:
        started.set()
        finish.wait(5)
        return {"available": True, "value": "pre-write"}

    worker = threading.Thread(target=board_mod._cached, args=("k", slow_old))
    worker.start()
    assert started.wait(5)
    board_mod._invalidate()  # a write lands while the read is in flight
    finish.set()
    worker.join(5)
    fresh = board_mod._cached("k", lambda: {"available": True, "value": "post-write"})
    assert fresh["value"] == "post-write"
    board_mod.reset_cache()


@pytest.mark.unit
def test_concurrent_cache_misses_share_one_load() -> None:
    board_mod.reset_cache()
    calls: list[int] = []
    gate = threading.Event()

    def load() -> dict[str, Any]:
        calls.append(1)
        gate.wait(5)
        return {"available": True}

    threads = [threading.Thread(target=board_mod._cached, args=("k", load)) for _ in range(4)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(5)
    assert len(calls) == 1
    board_mod.reset_cache()


# ── 14: per-repo fallback is never reported complete ────────────────────────
@pytest.mark.unit
def test_per_repo_fallback_is_incomplete_with_warning(rm: FakeRM, client: TestClient) -> None:
    rm.disable_all_repos()
    rm.respond("agent_communicate:list", board(session("s1", "Tools")))
    body = client.get("/api/coordination/sessions").json()
    assert body["available"] is True and body["complete"] is False
    assert any("--all-repos" in w for w in body["warnings"])


# ── 15: RM lease scripts report ``errors: [...]`` and partial outcomes ──────
@pytest.mark.unit
def test_partial_lease_post_is_200_with_warnings(rm: FakeRM, client: TestClient) -> None:
    rm.respond("check_agent_claim", claim_status(False))
    rm.respond("post_agent_lease", lease_result(label=False, errors=["label: CalledProcessError: 403"]))
    resp = client.post("/api/coordination/claims", json=_CLAIM, headers=_BOT)
    assert resp.status_code == 200, resp.text
    assert resp.json()["warnings"] == ["label: CalledProcessError: 403"]


@pytest.mark.unit
def test_lease_comment_failure_is_502_with_rm_errors(rm: FakeRM, client: TestClient) -> None:
    rm.respond("check_agent_claim", claim_status(False))
    errs = ["comment: CalledProcessError: HTTP 502"]
    rm.respond("post_agent_lease", lease_result(comment=False, errors=errs))
    resp = client.post("/api/coordination/claims", json=_CLAIM, headers=_BOT)
    assert resp.status_code == 502 and "HTTP 502" in resp.json()["detail"]["error"]


@pytest.mark.unit
def test_release_errors_are_surfaced(rm: FakeRM, client: TestClient) -> None:
    errs = ["Release requires the current lease's exact agent and session; no mutation performed"]
    rm.respond("release_agent_lease", release_result(label=False, comment=False, errors=errs), rc=1)
    resp = client.post("/api/coordination/claims/release", json=_RELEASE, headers=_BOT)
    assert resp.status_code == 502 and "exact agent and session" in resp.json()["detail"]["error"]
    partial = ["read-after-comment: CalledProcessError: 500"]
    rm.respond("release_agent_lease", release_result(label=False, errors=partial), rc=1)
    resp = client.post("/api/coordination/claims/release", json=_RELEASE, headers=_BOT)
    assert resp.status_code == 200 and resp.json()["warnings"] == partial
