"""Contract tests for the SQLite WAL lease store (#3878).

Mirrors the ownership/quota contract of ``runner_lease.LeaseManager`` plus
expiry, fencing and crash-recovery guarantees specific to the SQLite backend.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from runner_lease import LeaseRecord
from runner_lease_sqlite import SqliteLeaseStore, StaleFencingTokenError


def _make_store(tmp_path: Path) -> SqliteLeaseStore:
    return SqliteLeaseStore(tmp_path / "leases.db")


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def test_acquire_happy_path(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    rec = store.acquire("p1", "runner-1", max_runners=2)
    assert rec.principal_id == "p1"
    assert rec.runner_id == "runner-1"
    assert rec.expires_at is not None


def test_acquire_same_principal_is_idempotent_update(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    first = store.acquire("p1", "runner-1", max_runners=2)
    second = store.acquire("p1", "runner-1", max_runners=2, task_id="t2")
    assert second.acquired_at == first.acquired_at
    assert second.task_id == "t2"
    assert len(store.read_all()) == 1


def test_acquire_conflicting_principal_rejected(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.acquire("p1", "runner-1", max_runners=2)
    with pytest.raises(ValueError, match="already leased"):
        store.acquire("p2", "runner-1", max_runners=2)


def test_acquire_over_quota_rejected(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.acquire("p1", "runner-1", max_runners=1)
    with pytest.raises(PermissionError, match="quota"):
        store.acquire("p1", "runner-2", max_runners=1)


def test_release_removes_lease(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.acquire("p1", "runner-1", max_runners=2)
    assert store.release("runner-1") is True
    assert store.read_all() == []


def test_release_missing_lease_is_noop(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.release("runner-missing") is False


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def test_expired_lease_excluded_from_read_all(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.acquire("p1", "runner-1", max_runners=2, duration_seconds=-1)
    assert store.read_all() == []


def test_prune_expired_returns_removed_count(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.acquire("p1", "runner-1", max_runners=2, duration_seconds=-1)
    # acquire() purges expired rows itself as part of its own quota check, so
    # a second unrelated acquire must not be interposed before the explicit
    # prune_expired() call under test -- it would silently consume the
    # expired row first and make this assertion flaky by construction.
    assert store.prune_expired() == 1
    assert store.prune_expired() == 0


def test_expired_lease_does_not_count_against_quota(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.acquire("p1", "runner-1", max_runners=1, duration_seconds=-1)
    # The expired lease must not block a fresh acquisition under the same quota.
    rec = store.acquire("p1", "runner-2", max_runners=1)
    assert rec.runner_id == "runner-2"


# ---------------------------------------------------------------------------
# Fencing
# ---------------------------------------------------------------------------


def test_fencing_token_increments_on_renewal(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    first = store.acquire("p1", "runner-1", max_runners=2)
    second = store.acquire("p1", "runner-1", max_runners=2)
    assert second.fencing_token == first.fencing_token + 1


def test_release_with_stale_fencing_token_rejected(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    rec = store.acquire("p1", "runner-1", max_runners=2)
    store.acquire("p1", "runner-1", max_runners=2)  # bumps the token
    with pytest.raises(StaleFencingTokenError):
        store.release("runner-1", fencing_token=rec.fencing_token)


def test_release_with_current_fencing_token_succeeds(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    rec = store.acquire("p1", "runner-1", max_runners=2)
    assert store.release("runner-1", fencing_token=rec.fencing_token) is True


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------


def test_wal_mode_enabled(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    (mode,) = store._execute("PRAGMA journal_mode").fetchone()
    assert mode.lower() == "wal"


def test_record_survives_reopen_without_clean_close(tmp_path: Path) -> None:
    """A fresh connection to the same file sees committed writes (WAL crash-safety).

    Autocommit (``isolation_level=None``) durably commits every acquire on
    return, so never calling ``close()`` on the first handle -- modeling a
    crash right after that commit -- must not lose or corrupt state.
    """
    db_path = tmp_path / "leases.db"
    store = SqliteLeaseStore(db_path)
    store.acquire("p1", "runner-1", max_runners=2)
    # No store.close() -- simulates the process dying right after the commit.

    reopened = SqliteLeaseStore(db_path)
    records = reopened.read_all()
    assert len(records) == 1
    assert records[0].runner_id == "runner-1"

    conn = sqlite3.connect(str(db_path))
    (status,) = conn.execute("PRAGMA integrity_check").fetchone()
    conn.close()
    assert status == "ok"


# ---------------------------------------------------------------------------
# LeaseStore protocol conformance (dual-read/drain/rollback harness, #3877)
# ---------------------------------------------------------------------------


def test_write_all_replaces_contents_for_drain(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.acquire("p1", "runner-1", max_runners=2)
    snapshot = [
        LeaseRecord(principal_id="p2", runner_id="runner-2", acquired_at=1.0, expires_at=None),
    ]
    store.write_all(snapshot)
    records = store.read_all()
    assert [r.runner_id for r in records] == ["runner-2"]


def test_read_all_round_trips_through_write_all(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    snapshot = [
        LeaseRecord(
            principal_id="p1",
            runner_id="runner-1",
            acquired_at=10.0,
            expires_at=None,
            task_id="t1",
            metadata={"k": "v"},
        ),
    ]
    store.write_all(snapshot)
    (rec,) = store.read_all()
    assert rec.principal_id == "p1"
    assert rec.task_id == "t1"
    assert rec.metadata == {"k": "v"}
