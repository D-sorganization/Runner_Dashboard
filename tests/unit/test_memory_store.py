"""Unit tests for assistant memory SQLite store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from memory.models import MemoryEntry
from memory.store import AssistantMemoryStore, new_entry_id, reset_memory_store

pytestmark = pytest.mark.unit


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AssistantMemoryStore:
    monkeypatch.setenv("ASSISTANT_MEMORY_DB", str(tmp_path / "mem.sqlite3"))
    reset_memory_store()
    return AssistantMemoryStore(tmp_path / "mem.sqlite3")


def _entry(**kwargs: object) -> MemoryEntry:
    base = {
        "id": new_entry_id(),
        "entry_type": "session_digest",
        "author_principal": "principal:agent-grok",
        "source_product": "grokbot",
        "tags": ["Runner_Dashboard"],
        "body": {"summary": "test"},
        "created_at": "2026-10-21T12:00:00Z",
    }
    base.update(kwargs)
    return MemoryEntry(**base)  # type: ignore[arg-type]


def test_append_and_list_excludes_superseded(store: AssistantMemoryStore) -> None:
    first = _entry()
    store.append(first)
    second = _entry(supersedes_id=first.id)
    store.append(second)
    rows = store.list_entries(entry_type="session_digest", limit=10)
    active = [r for r in rows if not r.superseded_by]
    assert len(active) == 1
    assert active[0].id == second.id
    assert store.get(first.id) is not None
    assert store.get(first.id).superseded_by == second.id


def test_idempotent_session_digest_append_replays_one_entry(store: AssistantMemoryStore) -> None:
    first, replayed = store.append_idempotent(_entry(), idempotency_key="key-1", fingerprint="digest-1")
    second, was_replay = store.append_idempotent(
        _entry(body={"summary": "different entry id, same request"}),
        idempotency_key="key-1",
        fingerprint="digest-1",
    )

    assert first.id == second.id
    assert replayed is False
    assert was_replay is True
    assert len(store.list_entries(entry_type="session_digest", limit=10)) == 1


def test_idempotent_session_digest_append_rejects_changed_request(store: AssistantMemoryStore) -> None:
    store.append_idempotent(_entry(), idempotency_key="key-1", fingerprint="digest-1")

    with pytest.raises(ValueError, match="idempotency_key_reused"):
        store.append_idempotent(_entry(), idempotency_key="key-1", fingerprint="digest-2")

    assert len(store.list_entries(entry_type="session_digest", limit=10)) == 1


def test_concurrent_idempotent_session_digest_appends_insert_once(store: AssistantMemoryStore, tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    second_connection = AssistantMemoryStore(tmp_path / "mem.sqlite3")

    def append(index: int) -> tuple[MemoryEntry, bool]:
        connection = store if index % 2 else second_connection
        return connection.append_idempotent(_entry(), idempotency_key="key-concurrent", fingerprint="same-request")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(append, range(8)))

    assert len({entry.id for entry, _ in results}) == 1
    assert sum(replayed for _, replayed in results) == 7
    assert len(store.list_entries(entry_type="session_digest", limit=10)) == 1


def test_idempotency_insert_failure_rolls_back_entry(store: AssistantMemoryStore) -> None:
    store._db.execute(
        """
        CREATE TRIGGER reject_digest_idempotency
        BEFORE INSERT ON memory_idempotency
        WHEN NEW.idempotency_key = 'new-key'
        BEGIN
          SELECT RAISE(ABORT, 'forced idempotency insert failure');
        END
        """
    )
    candidate = _entry()

    with pytest.raises(sqlite3.IntegrityError):
        store.append_idempotent(candidate, idempotency_key="new-key", fingerprint="new-fingerprint")

    assert store.get(candidate.id) is None
    assert (
        store._db.execute("SELECT 1 FROM memory_idempotency WHERE idempotency_key = ?", ("new-key",)).fetchone() is None
    )


def test_unicode_digest_bytes_count_toward_later_generic_write_cap(
    store: AssistantMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import memory.store as memory_store

    monkeypatch.setattr(memory_store, "MAX_STORE_BYTES", 100)
    store.append_idempotent(
        _entry(body={"summary": "é" * 40}),
        idempotency_key="unicode-digest",
        fingerprint="unicode-digest-v1",
    )

    with pytest.raises(ValueError, match="store_size_cap_exceeded"):
        store.append(_entry(entry_type="task", body={"summary": "x" * 30}))
