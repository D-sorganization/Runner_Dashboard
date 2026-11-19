"""#4270 B-09: job-board fences and lease TTL rows survive a dashboard restart.

Both lived in process dicts. After a restart every holder's fence stopped matching, so it
could neither heartbeat nor release, and the sweeper had no row left to expire.
``reset_job_board_claim_state`` stands in for the restart: it drops the process's handle on
the claim-state database and nothing else.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from coordination import claim_state as claim_state_mod
from coordination import claims as claims_mod
from coordination import job_board_lease
from coordination import jobs as jobs_mod
from coordination.board import RMScriptError
from coordination.claim_state import ClaimStateStore
from coordination.job_board_rows import JobLeaseRow

REPO = "Tools"
AGENT = "claude"
SESSION = "claude-node-b-20261114-b09"
MAX = job_board_lease.MAX_JOB_RELEASE_ATTEMPTS

restart = jobs_mod.reset_job_board_claim_state


class FakeLeases:
    """RM stand-in: one holder per issue; a release succeeds only for that holder."""

    def __init__(self) -> None:
        self.holders: dict[tuple[str, int], tuple[str, str]] = {}
        self.failing: set[int] = set()
        self.released: list[int] = []

    def check(self, repo: str, issue: int) -> dict[str, Any]:
        agent, session = self.holders.get((repo, issue), ("", ""))
        return {"available": True, "held": bool(agent), "agent": agent, "session": session, "expires_at": None}

    def release(self, repo: str, issue: int, *, agent: str, session: str, **_kw: Any) -> dict[str, Any]:
        if issue in self.failing:
            raise RMScriptError("release_agent_lease failed: HTTP 502")
        assert self.holders.pop((repo, issue)) == (agent, session)
        self.released.append(issue)
        return {"released": True, "result": {}, "warnings": []}

    def post_lease(self, repo: str, issue: int, agent: str, session: str, *_a: Any, **_kw: Any) -> dict[str, Any]:
        return {"result": {"expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat()}, "warnings": []}


@pytest.fixture
def leases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeLeases]:
    monkeypatch.setenv("CLAIM_STATE_DB", str(tmp_path / "claim_state.sqlite3"))
    claim_state_mod.reset_claim_state_store()
    fake = FakeLeases()
    monkeypatch.setattr(claims_mod, "check", fake.check)
    monkeypatch.setattr(claims_mod, "release", fake.release)
    monkeypatch.setattr(claims_mod, "post_lease", fake.post_lease)
    yield fake
    claim_state_mod.reset_claim_state_store()


def _held_job(leases: FakeLeases, issue: int, *, expires_in_minutes: float) -> int:
    """A job held by ``SESSION``, as ``claim_job`` leaves it; returns its fence."""
    leases.holders[(REPO, issue)] = (AGENT, SESSION)
    fence = jobs_mod._mint_fence(REPO, issue)
    expires = (datetime.now(UTC) + timedelta(minutes=expires_in_minutes)).isoformat()
    job_board_lease.remember_lease(
        REPO, issue, agent=AGENT, session=SESSION, fence=fence, ttl_hours=0.5, expires_at=expires
    )
    return fence


def _tracked() -> set[int]:
    return {row.issue for row in job_board_lease.tracked_leases()}


@pytest.mark.unit
def test_a_holder_can_release_with_its_fence_after_a_restart(leases: FakeLeases) -> None:
    fence = _held_job(leases, 31, expires_in_minutes=60)

    restart()

    assert jobs_mod._current_fence(REPO, 31) == fence
    result = jobs_mod.release_job(REPO, 31, agent=AGENT, session=SESSION, fence=fence, reason="done")
    assert result["released"] is True and leases.released == [31]
    assert _tracked() == set()


@pytest.mark.unit
def test_a_holder_can_heartbeat_with_its_fence_after_a_restart(leases: FakeLeases) -> None:
    fence = _held_job(leases, 32, expires_in_minutes=1)

    restart()
    beat = jobs_mod.heartbeat_job(REPO, 32, agent=AGENT, session=SESSION, fence=fence)

    assert beat["heartbeated"] is True
    (row,) = job_board_lease.tracked_leases()
    assert row.fence == fence and row.expires_at > (datetime.now(UTC) + timedelta(hours=1)).timestamp()


@pytest.mark.unit
def test_fences_keep_rising_across_a_restart(leases: FakeLeases) -> None:
    """A fence minted before the restart is never issued again after it."""
    first = jobs_mod._mint_fence(REPO, 33)
    second = jobs_mod._mint_fence(REPO, 33)

    restart()
    third = jobs_mod._mint_fence(REPO, 33)

    assert (first, second, third) == (1, 2, 3)
    with pytest.raises(jobs_mod.StaleFenceError):
        jobs_mod._require_fence(REPO, 33, second)
    assert jobs_mod._current_fence("tools", 33) == 3, "repo names are compared without regard to case"
    assert jobs_mod._current_fence(REPO, 34) == 0


@pytest.mark.unit
def test_the_sweeper_releases_a_lease_that_expired_while_the_dashboard_was_down(leases: FakeLeases) -> None:
    _held_job(leases, 35, expires_in_minutes=-5)
    _held_job(leases, 36, expires_in_minutes=60)

    restart()
    result = jobs_mod.sweep_expired_job_claims()

    assert result == {"released": 1, "errors": [], "forgotten": 0, "dead_lettered": 0}
    assert leases.released == [35] and _tracked() == {36}


@pytest.mark.unit
def test_a_restart_does_not_refill_the_release_retry_budget(
    leases: FakeLeases, caplog: pytest.LogCaptureFixture
) -> None:
    _held_job(leases, 37, expires_in_minutes=-5)
    leases.failing.add(37)
    caplog.set_level(logging.ERROR, logger="dashboard.coordination")
    for _ in range(MAX - 2):
        jobs_mod.sweep_expired_job_claims()

    restart()
    before_last = jobs_mod.sweep_expired_job_claims()
    restart()
    last = jobs_mod.sweep_expired_job_claims()

    assert before_last["dead_lettered"] == 0 and _tracked() == set() and last["dead_lettered"] == 1
    assert sum("dead-letter" in rec.getMessage() for rec in caplog.records) == 1


@pytest.mark.unit
def test_a_renewal_made_during_a_sweep_is_not_forgotten_or_counted(leases: FakeLeases) -> None:
    """The sweeper only settles the lease it read; a claim or renewal in between wins."""
    fence = _held_job(leases, 38, expires_in_minutes=-5)
    store = claim_state_mod.get_store()
    (stale,) = store.list_job_leases()

    job_board_lease.remember_lease(REPO, 38, agent=AGENT, session=SESSION, fence=fence, ttl_hours=1, expires_at=None)

    assert store.count_job_release_failure(stale) == 0
    assert store.forget_job_lease(stale) is False
    (current,) = store.list_job_leases()
    assert current.release_failures == 0 and current.expires_at > stale.expires_at


@pytest.mark.unit
def test_two_workers_on_one_database_never_mint_the_same_fence(tmp_path: Path) -> None:
    path = tmp_path / "shared.sqlite3"
    workers = [ClaimStateStore(path), ClaimStateStore(path)]
    minted: list[int] = []

    def mint(store: ClaimStateStore) -> None:
        for _ in range(25):
            minted.append(store.mint_fence(REPO, 39))

    threads = [threading.Thread(target=mint, args=(store,)) for store in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for store in workers:
        store.close()

    assert sorted(minted) == list(range(1, 51))


@pytest.mark.unit
def test_lease_rows_round_trip_and_purge(tmp_path: Path) -> None:
    store = ClaimStateStore(tmp_path / "rows.sqlite3")
    row = JobLeaseRow("Tools", 40, AGENT, SESSION, 7, 1_800_000_000.5, 0.5)

    store.put_job_lease(row)
    assert store.get_job_lease("TOOLS", 40) == row
    assert store.count_job_release_failure(row) == 1
    store.put_job_lease(row)
    assert store.get_job_lease("Tools", 40) == row, "a claim or renewal resets the failure count"

    store.mint_fence("Tools", 40)
    store.purge_job_board_rows()
    assert store.list_job_leases() == [] and store.current_fence("Tools", 40) == 0
    store.close()


@pytest.mark.unit
def test_the_job_board_keeps_no_fence_or_lease_table_in_process_memory() -> None:
    for module in (jobs_mod, job_board_lease):
        assert not hasattr(module, "_fences") and not hasattr(module, "_lease_state"), module.__name__


class _CommitFailsOnce:
    """A connection whose first ``COMMIT`` fails, as on a full disk."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self.failed = False

    def execute(self, sql: str, *args: Any) -> sqlite3.Cursor:
        if sql == "COMMIT" and not self.failed:
            self.failed = True
            raise sqlite3.OperationalError("disk I/O error")
        return self._db.execute(sql, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)


@pytest.mark.unit
def test_a_failed_commit_is_rolled_back_and_the_store_keeps_working(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ClaimStateStore(tmp_path / "commit.sqlite3")
    monkeypatch.setattr(store, "_db", _CommitFailsOnce(store._db))

    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        store.mint_fence(REPO, 41)

    assert store.current_fence(REPO, 41) == 0, "the failed mint left nothing behind"
    assert store.mint_fence(REPO, 41) == 1, "the connection is not stuck inside the failed transaction"
    assert store.try_acquire(REPO, 41, agent=AGENT, session=SESSION).acquired


@pytest.mark.unit
def test_a_store_error_on_one_swept_row_does_not_stop_the_others(
    leases: FakeLeases, monkeypatch: pytest.MonkeyPatch
) -> None:
    _held_job(leases, 42, expires_in_minutes=-5)
    _held_job(leases, 43, expires_in_minutes=-5)
    leases.failing.add(42)

    def locked(_self: ClaimStateStore, _row: JobLeaseRow) -> int:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ClaimStateStore, "count_job_release_failure", locked)

    result = jobs_mod.sweep_expired_job_claims()

    assert result["released"] == 1 and leases.released == [43]
    assert len(result["errors"]) == 1 and "Tools#42" in result["errors"][0]
    assert _tracked() == {42}, "the row stays for the next sweep"
