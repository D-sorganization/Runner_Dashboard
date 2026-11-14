"""#4270 B-10: the agent-bus reaper recovers a dead agent's claims truthfully.

The RM lease is keyed by the fleet agent (``claim:<agent>``), not by the bus
role a session registered with. Releasing with the role is refused by RM, so a
dead session's claim was retried every tick forever and never recovered.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from agent_bus import reaper, store_ops
from agent_bus.reaper_stats import reset_reaper_stats_for_tests, snapshot
from agent_bus.store import AgentBusStore
from coordination import claims as claims_mod
from coordination.board import RMScriptError
from identity import Principal
from staff import store as staff_store_mod

SESSION = "claude-node-b-20261112-b10"
ROLE = "issue-remediator"
LEASE_AGENT = "claude"
REPO = "Tools"


class FakeLeases:
    """RM stand-in: a release succeeds only for the exact holder agent and session."""

    def __init__(self) -> None:
        self.holders: dict[tuple[str, int], tuple[str, str]] = {}
        self.available = True
        self.release_calls: list[dict[str, Any]] = []

    def check(self, repo: str, issue: int) -> dict[str, Any]:
        if not self.available:
            return {"available": False, "reason": "gh timeout", "held": None}
        agent, session = self.holders.get((repo, issue), ("", ""))
        return {
            "available": True,
            "held": bool(agent),
            "agent": agent,
            "session": session,
            "reason": "active lease" if agent else "",
            "expires_at": None,
        }

    def release(self, repo: str, issue: int, *, agent: str, session: str, reason: str, **_kw: Any) -> dict[str, Any]:
        self.release_calls.append({"repo": repo, "issue": issue, "agent": agent, "session": session})
        if self.holders.get((repo, issue)) != (agent, session):
            raise RMScriptError("release_agent_lease failed: Release requires the current lease's exact agent")
        del self.holders[(repo, issue)]
        return {"released": True, "result": {}, "warnings": []}


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    staff_store_mod.reset_store()
    reset_reaper_stats_for_tests()
    return tmp_path / "bus.sqlite3"


@pytest.fixture
def bus(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> AgentBusStore:
    store = AgentBusStore(db_path)
    monkeypatch.setattr(reaper, "get_store", lambda: store)
    return store


@pytest.fixture
def leases(monkeypatch: pytest.MonkeyPatch) -> FakeLeases:
    fake = FakeLeases()
    monkeypatch.setattr(claims_mod, "check", fake.check)
    monkeypatch.setattr(claims_mod, "release", fake.release)
    monkeypatch.setattr(reaper.coordination_jobs, "sweep_expired_job_claims", lambda: {"released": 0, "errors": []})
    return fake


def _dead_session_with_claims(bus: AgentBusStore, *issues: int) -> None:
    bus.register(
        session_id=SESSION,
        role=ROLE,
        provider=LEASE_AGENT,
        node="node-b",
        principal=Principal(id="agent-fleet", type="bot", name="fleet", roles=["bot"]),
        lease_seconds=600,
        mint_token=False,
    )
    for issue in issues:
        bus.track_claim(SESSION, ROLE, REPO, issue)
    bus.expire_session(SESSION)


def _reap() -> dict[str, int]:
    return reaper.reap_once(allows_reap=lambda _node: True)


@pytest.mark.unit
def test_reaper_releases_with_the_lease_holder_agent_not_the_bus_role(bus: AgentBusStore, leases: FakeLeases) -> None:
    leases.holders[(REPO, 11)] = (LEASE_AGENT, SESSION)
    _dead_session_with_claims(bus, 11)

    result = _reap()

    assert [call["agent"] for call in leases.release_calls] == [LEASE_AGENT]
    assert result["released_claims"] == 1
    assert leases.holders == {}
    assert bus.get_principal(SESSION) is None


@pytest.mark.unit
def test_reaper_never_releases_a_claim_another_session_now_holds(bus: AgentBusStore, leases: FakeLeases) -> None:
    leases.holders[(REPO, 12)] = ("codex", "codex-node-b-20261112-other")
    _dead_session_with_claims(bus, 12)

    result = _reap()

    assert leases.release_calls == []
    assert result["released_claims"] == 0
    assert leases.holders == {(REPO, 12): ("codex", "codex-node-b-20261112-other")}
    assert bus.get_principal(SESSION) is None


@pytest.mark.unit
def test_reaper_dead_letters_a_claim_after_max_failed_releases(
    bus: AgentBusStore, db_path: Path, leases: FakeLeases, caplog: pytest.LogCaptureFixture
) -> None:
    leases.available = False  # RM cannot answer: every attempt fails
    _dead_session_with_claims(bus, 13)
    caplog.set_level(logging.WARNING, logger="dashboard.agent_bus")

    for _ in range(store_ops.MAX_CLAIM_RELEASE_ATTEMPTS - 1):
        _reap()
        assert snapshot()["errors"] == 1
    assert bus.get_principal(SESSION) is not None, "retained while retries remain"
    assert "dead-letter" not in caplog.text

    # The failure count is durable: a restarted hub (fresh store) does not start over.
    restarted = AgentBusStore(db_path)
    assert restarted.release_tracked_claims(SESSION, ROLE, reason="agent_bus lease expired") == 0

    dead = [rec for rec in caplog.records if "dead-letter" in rec.getMessage()]
    assert len(dead) == 1 and dead[0].levelno == logging.ERROR
    assert f"{REPO}#13" in dead[0].getMessage() and SESSION in dead[0].getMessage()

    _reap()  # nothing left to retry: the stale session is finally cleared
    assert bus.get_principal(SESSION) is None
    assert leases.release_calls == []


@pytest.mark.unit
def test_reaper_keeps_partial_progress_when_one_release_fails(
    bus: AgentBusStore, leases: FakeLeases, monkeypatch: pytest.MonkeyPatch
) -> None:
    leases.holders[(REPO, 14)] = (LEASE_AGENT, SESSION)
    leases.holders[(REPO, 15)] = (LEASE_AGENT, SESSION)
    _dead_session_with_claims(bus, 14, 15)
    real_release = leases.release

    def flaky_release(repo: str, issue: int, **kwargs: Any) -> dict[str, Any]:
        if issue == 15:
            leases.release_calls.append({"repo": repo, "issue": issue, **kwargs})
            raise RMScriptError("release_agent_lease failed: HTTP 502")
        return real_release(repo, issue, **kwargs)

    monkeypatch.setattr(claims_mod, "release", flaky_release)
    first = _reap()
    second = _reap()

    assert first["released_claims"] == 1 and second["released_claims"] == 0
    assert [call["issue"] for call in leases.release_calls] == [14, 15, 15], "the released claim is not retried"
    assert bus.get_principal(SESSION) is not None


@pytest.mark.unit
@pytest.mark.parametrize(("session_id", "reason"), [("", "expired"), (SESSION, " ")])
def test_release_tracked_claims_rejects_blank_inputs(bus: AgentBusStore, session_id: str, reason: str) -> None:
    with pytest.raises(ValueError):
        bus.release_tracked_claims(session_id, ROLE, reason=reason)


@pytest.mark.unit
def test_existing_bus_database_gains_the_failure_counter(db_path: Path) -> None:
    import sqlite3

    legacy = sqlite3.connect(str(db_path))
    legacy.execute(
        "CREATE TABLE agent_bus_claims (session_id TEXT NOT NULL, repo TEXT NOT NULL, issue INTEGER NOT NULL, "
        "role TEXT NOT NULL, claimed_at TEXT NOT NULL, PRIMARY KEY (session_id, repo, issue))"
    )
    legacy.execute("INSERT INTO agent_bus_claims VALUES (?, ?, 16, ?, '2026-11-12T00:00:00Z')", (SESSION, REPO, ROLE))
    legacy.commit()
    legacy.close()

    upgraded = AgentBusStore(db_path)
    AgentBusStore(db_path)  # idempotent on a second boot

    row = upgraded._conn().execute("SELECT release_failures FROM agent_bus_claims WHERE issue = 16").fetchone()
    assert row["release_failures"] == 0
