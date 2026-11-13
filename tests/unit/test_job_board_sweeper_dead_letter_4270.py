"""#4270 B-11: the job-board expiry sweeper settles every row instead of retrying forever.

A row whose release kept failing stayed in the in-process lease table and was
retried on every reaper tick with no cap. A row whose job had moved to another
holder (stale fence) was retried the same way, although nothing was left to
release.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from coordination import claims as claims_mod
from coordination import job_board_lease
from coordination import jobs as jobs_mod
from coordination.board import RMScriptError

REPO = "Tools"
AGENT = "claude"
SESSION = "claude-node-b-20261113-b11"
OTHER = ("codex", "codex-hub-a-20261113-other")
MAX = job_board_lease.MAX_JOB_RELEASE_ATTEMPTS


class FakeLeases:
    """RM stand-in: a release succeeds only for the exact holder agent and session."""

    def __init__(self) -> None:
        self.holders: dict[tuple[str, int], tuple[str, str]] = {}
        self.failing: set[int] = set()
        self.release_calls: list[dict[str, Any]] = []

    def check(self, repo: str, issue: int) -> dict[str, Any]:
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
        if issue in self.failing:
            raise RMScriptError("release_agent_lease failed: HTTP 502")
        if self.holders.get((repo, issue)) != (agent, session):
            raise RMScriptError("release_agent_lease failed: Release requires the current lease's exact agent")
        del self.holders[(repo, issue)]
        return {"released": True, "result": {}, "warnings": []}


@pytest.fixture
def leases(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeLeases]:
    jobs_mod.reset_job_board_claim_state()
    fake = FakeLeases()
    monkeypatch.setattr(claims_mod, "check", fake.check)
    monkeypatch.setattr(claims_mod, "release", fake.release)
    yield fake
    jobs_mod.reset_job_board_claim_state()


def _expired_claim(leases: FakeLeases, issue: int) -> int:
    """Seed a claim held by ``SESSION`` whose in-process TTL has already lapsed."""
    leases.holders[(REPO, issue)] = (AGENT, SESSION)
    fence = jobs_mod._mint_fence(REPO, issue)
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    job_board_lease.remember_lease(
        REPO, issue, agent=AGENT, session=SESSION, fence=fence, ttl_hours=0.1, expires_at=past
    )
    return fence


def _tracked() -> set[int]:
    return {row[1] for row in job_board_lease._lease_state.values()}


def _dead_letters(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [rec for rec in caplog.records if "dead-letter" in rec.getMessage()]


@pytest.mark.unit
def test_sweeper_releases_as_the_holder_agent(leases: FakeLeases) -> None:
    _expired_claim(leases, 21)

    result = jobs_mod.sweep_expired_job_claims()

    assert leases.release_calls == [{"repo": REPO, "issue": 21, "agent": AGENT, "session": SESSION}]
    assert result["released"] == 1 and result["errors"] == []
    assert _tracked() == set() and leases.holders == {}


@pytest.mark.unit
def test_sweeper_dead_letters_a_row_after_max_failed_releases(
    leases: FakeLeases, caplog: pytest.LogCaptureFixture
) -> None:
    _expired_claim(leases, 22)
    leases.failing.add(22)
    caplog.set_level(logging.WARNING, logger="dashboard.coordination")

    for _ in range(MAX - 1):
        result = jobs_mod.sweep_expired_job_claims()
        assert len(result["errors"]) == 1 and result["dead_lettered"] == 0
    assert _tracked() == {22}, "retained while retries remain"
    assert _dead_letters(caplog) == []

    final = jobs_mod.sweep_expired_job_claims()

    assert final["dead_lettered"] == 1 and final["released"] == 0
    dead = _dead_letters(caplog)
    assert len(dead) == 1 and dead[0].levelno == logging.ERROR
    assert f"{REPO}#22" in dead[0].getMessage() and SESSION in dead[0].getMessage()
    assert _tracked() == set()

    for _ in range(3):  # nothing left to retry
        assert jobs_mod.sweep_expired_job_claims() == {"released": 0, "errors": [], "forgotten": 0, "dead_lettered": 0}
    assert len(leases.release_calls) == MAX
    assert len(_dead_letters(caplog)) == 1


@pytest.mark.unit
def test_sweeper_releases_after_a_transient_failure_and_starts_the_next_claim_clean(
    leases: FakeLeases, caplog: pytest.LogCaptureFixture
) -> None:
    _expired_claim(leases, 23)
    leases.failing.add(23)
    caplog.set_level(logging.WARNING, logger="dashboard.coordination")
    for _ in range(MAX - 1):
        jobs_mod.sweep_expired_job_claims()

    leases.failing.clear()
    recovered = jobs_mod.sweep_expired_job_claims()

    assert recovered["released"] == 1 and recovered["errors"] == [] and recovered["dead_lettered"] == 0
    assert _tracked() == set() and leases.holders == {}

    # A later claim on the same issue gets a full retry budget, not the old count.
    _expired_claim(leases, 23)
    leases.failing.add(23)
    again = jobs_mod.sweep_expired_job_claims()
    assert again["dead_lettered"] == 0 and _tracked() == {23}
    assert _dead_letters(caplog) == []


@pytest.mark.unit
def test_sweeper_forgets_a_row_another_holder_now_owns_without_releasing(leases: FakeLeases) -> None:
    _expired_claim(leases, 24)
    leases.holders[(REPO, 24)] = OTHER

    first = jobs_mod.sweep_expired_job_claims()
    second = jobs_mod.sweep_expired_job_claims()

    assert leases.release_calls == []
    assert leases.holders == {(REPO, 24): OTHER}
    assert first == {"released": 0, "errors": [], "forgotten": 1, "dead_lettered": 0}
    assert second["forgotten"] == 0 and _tracked() == set()


@pytest.mark.unit
def test_sweeper_forgets_a_row_whose_fence_was_superseded(leases: FakeLeases) -> None:
    _expired_claim(leases, 25)
    jobs_mod._mint_fence(REPO, 25)  # a newer claim epoch exists for this issue

    result = jobs_mod.sweep_expired_job_claims()

    assert leases.release_calls == []
    assert result["forgotten"] == 1 and result["errors"] == [] and _tracked() == set()


@pytest.mark.unit
def test_one_failing_row_does_not_block_the_others_in_the_same_tick(leases: FakeLeases) -> None:
    for issue in (26, 27, 28):
        _expired_claim(leases, issue)
    leases.failing.add(26)
    leases.holders[(REPO, 27)] = OTHER

    result = jobs_mod.sweep_expired_job_claims()

    assert result["released"] == 1 and result["forgotten"] == 1 and len(result["errors"]) == 1
    assert _tracked() == {26}
    assert [call["issue"] for call in leases.release_calls] == [26, 28]


@pytest.mark.unit
def test_heartbeat_bookkeeping_resets_the_failure_count(leases: FakeLeases, caplog: pytest.LogCaptureFixture) -> None:
    fence = _expired_claim(leases, 29)
    leases.failing.add(29)
    caplog.set_level(logging.WARNING, logger="dashboard.coordination")
    for _ in range(MAX - 1):
        jobs_mod.sweep_expired_job_claims()

    # The holder came back and renewed (what heartbeat_job records), then died again.
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    job_board_lease.remember_lease(REPO, 29, agent=AGENT, session=SESSION, fence=fence, ttl_hours=0.1, expires_at=past)
    result = jobs_mod.sweep_expired_job_claims()

    assert result["dead_lettered"] == 0 and _tracked() == {29}
    assert _dead_letters(caplog) == []
