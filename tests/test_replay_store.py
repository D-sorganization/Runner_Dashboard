"""Tests for the SQLite-backed replay-protection store (issue #344)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from replay_store import ReplayStore, migrate_json_to_sqlite  # noqa: E402


@pytest.fixture
def store(tmp_path: Path) -> ReplayStore:
    """Return a fresh in-memory-equivalent replay store backed by a temp file."""
    s = ReplayStore(tmp_path / "replay.db", ttl_s=60, max_entries=10)
    yield s
    s.close()


# ─── Basic correctness ────────────────────────────────────────────────────────


def test_fresh_envelope_is_not_replay(store: ReplayStore) -> None:
    assert store.is_replay("env-001") is False


def test_recorded_envelope_is_replay(store: ReplayStore) -> None:
    store.record("env-001")
    assert store.is_replay("env-001") is True


def test_expired_envelope_is_not_replay(tmp_path: Path) -> None:
    s = ReplayStore(tmp_path / "replay.db", ttl_s=0)
    try:
        s.record("env-001")
        # TTL=0 means it expires immediately (expires_at <= now)
        time.sleep(0.01)
        assert s.is_replay("env-001") is False
    finally:
        s.close()


def test_record_idempotent(store: ReplayStore) -> None:
    store.record("env-001")
    store.record("env-001")  # second insert should not raise
    assert store.is_replay("env-001") is True


# ─── Purge ────────────────────────────────────────────────────────────────────


def test_purge_removes_expired_entries(tmp_path: Path) -> None:
    s = ReplayStore(tmp_path / "replay.db", ttl_s=0)
    try:
        s.record("env-001")
        s.record("env-002")
        time.sleep(0.01)
        deleted = s.purge_expired()
        assert deleted == 2
        assert s.is_replay("env-001") is False
    finally:
        s.close()


def test_purge_keeps_live_entries(store: ReplayStore) -> None:
    store.record("env-001")
    deleted = store.purge_expired()
    assert deleted == 0
    assert store.is_replay("env-001") is True


# ─── Size cap ────────────────────────────────────────────────────────────────


def test_size_cap_evicts_oldest_entries(tmp_path: Path) -> None:
    s = ReplayStore(tmp_path / "replay.db", ttl_s=3600, max_entries=5)
    try:
        for i in range(10):
            s.record(f"env-{i:04d}")
        # Table must have at most 5 entries
        (count,) = s._db.execute("SELECT COUNT(*) FROM processed").fetchone()
        assert count <= 5
    finally:
        s.close()


# ─── JSON migration ───────────────────────────────────────────────────────────


def test_migrate_json_imports_live_entries(tmp_path: Path) -> None:
    import json

    json_path = tmp_path / "processed_envelopes.json"
    future = time.time() + 3600
    json_path.write_text(json.dumps({"env-A": future, "env-B": future}))

    s = ReplayStore(tmp_path / "replay.db")
    try:
        count = migrate_json_to_sqlite(json_path, s)
        assert count == 2
        assert s.is_replay("env-A") is True
        assert s.is_replay("env-B") is True
    finally:
        s.close()


def test_migrate_json_skips_expired_entries(tmp_path: Path) -> None:
    import json

    json_path = tmp_path / "processed_envelopes.json"
    past = time.time() - 1
    json_path.write_text(json.dumps({"env-expired": past}))

    s = ReplayStore(tmp_path / "replay.db")
    try:
        count = migrate_json_to_sqlite(json_path, s)
        assert count == 0
        assert s.is_replay("env-expired") is False
    finally:
        s.close()


def test_migrate_json_noop_when_file_absent(tmp_path: Path) -> None:
    s = ReplayStore(tmp_path / "replay.db")
    try:
        count = migrate_json_to_sqlite(tmp_path / "nonexistent.json", s)
        assert count == 0
    finally:
        s.close()
