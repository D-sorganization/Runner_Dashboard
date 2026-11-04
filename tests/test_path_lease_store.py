"""Contract tests for the TTL path/issue lease store (#3931 S1).

Clean-room: built from public docs/behavior descriptions only (no source
read or copied). Mirrors the ``runner_lease_sqlite.SqliteLeaseStore`` WAL
pattern (busy-retry, WAL journal) for a new domain: leases keyed by
``(repo, scope)`` where ``scope`` is a path glob or ``issue:<n>``, used by
Cursor dispatch's pre-launch/pre-publish re-check hook (S2).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest
from path_lease_store import LeaseConflictError, PathLeaseStore


def _make_store(tmp_path: Path) -> PathLeaseStore:
    return PathLeaseStore(tmp_path / "path_leases.db")


def test_acquire_happy_path(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    rec = store.acquire("Runner_Dashboard", "backend/path_lease_store.py", "claude-a", ttl_seconds=3600)
    assert (rec.repo, rec.scope, rec.owner) == ("Runner_Dashboard", "backend/path_lease_store.py", "claude-a")
    assert rec.expires_at > rec.acquired_at
    assert rec.lease_id


def test_acquire_conflicting_owner_raises_409(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.acquire("Runner_Dashboard", "issue:3931", "claude-a", ttl_seconds=3600)
    with pytest.raises(LeaseConflictError):
        store.acquire("Runner_Dashboard", "issue:3931", "codex-g", ttl_seconds=3600)


def test_acquire_same_owner_same_scope_is_idempotent_renew(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    first = store.acquire("Runner_Dashboard", "issue:3931", "claude-a", ttl_seconds=60)
    second = store.acquire("Runner_Dashboard", "issue:3931", "claude-a", ttl_seconds=3600)
    assert second.lease_id == first.lease_id
    assert second.expires_at > first.expires_at
    assert len(store.read_all()) == 1


def test_expired_lease_auto_frees_and_check_reports_free(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.acquire("Runner_Dashboard", "issue:3931", "claude-a", ttl_seconds=0.01)
    time.sleep(0.05)
    assert store.check("Runner_Dashboard", "issue:3931") is None
    rec = store.acquire("Runner_Dashboard", "issue:3931", "codex-g", ttl_seconds=3600)
    assert rec.owner == "codex-g"


def test_renew_by_owner_extends_ttl(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    rec = store.acquire("Runner_Dashboard", "issue:3931", "claude-a", ttl_seconds=60)
    renewed = store.renew(rec.lease_id, "claude-a", ttl_seconds=3600)
    assert renewed.expires_at > rec.expires_at


@pytest.mark.parametrize(
    "make_bad_call",
    [
        lambda store, lease_id: store.renew(lease_id, "codex-g", ttl_seconds=3600),
        lambda store, lease_id: store.renew("not-a-real-lease-id", "claude-a", ttl_seconds=3600),
        lambda store, lease_id: store.release(lease_id, "codex-g"),
        lambda store, lease_id: store.release("not-a-real-lease-id", "claude-a"),
    ],
)
def test_renew_and_release_reject_wrong_owner_or_unknown_id(
    tmp_path: Path, make_bad_call: Callable[[PathLeaseStore, str], object]
) -> None:
    store = _make_store(tmp_path)
    rec = store.acquire("Runner_Dashboard", "issue:3931", "claude-a", ttl_seconds=3600)
    with pytest.raises(LeaseConflictError):
        make_bad_call(store, rec.lease_id)


def test_renew_expired_lease_raises_conflict(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    rec = store.acquire("Runner_Dashboard", "issue:3931", "claude-a", ttl_seconds=0.01)
    time.sleep(0.05)
    with pytest.raises(LeaseConflictError):
        store.renew(rec.lease_id, "claude-a", ttl_seconds=3600)


def test_release_by_owner_frees_the_scope_for_a_new_owner(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    rec = store.acquire("Runner_Dashboard", "issue:3931", "claude-a", ttl_seconds=3600)
    store.release(rec.lease_id, "claude-a")
    assert store.check("Runner_Dashboard", "issue:3931") is None
    assert store.acquire("Runner_Dashboard", "issue:3931", "codex-g", ttl_seconds=3600).owner == "codex-g"


def test_prune_expired_removes_only_stale_rows(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.acquire("Runner_Dashboard", "issue:1", "claude-a", ttl_seconds=0.01)
    store.acquire("Runner_Dashboard", "issue:2", "claude-a", ttl_seconds=3600)
    time.sleep(0.05)
    store.prune_expired()
    remaining = store.read_all()
    assert [r.scope for r in remaining] == ["issue:2"]


def test_store_is_durable_across_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "path_leases.db"
    PathLeaseStore(db_path).acquire("Runner_Dashboard", "issue:3931", "claude-a", ttl_seconds=3600)
    rec = PathLeaseStore(db_path).check("Runner_Dashboard", "issue:3931")
    assert rec is not None
    assert rec.owner == "claude-a"
