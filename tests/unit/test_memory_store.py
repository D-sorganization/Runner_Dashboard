"""Unit tests for assistant memory SQLite store."""

from __future__ import annotations

from pathlib import Path

import pytest
from memory.models import MemoryEntry
from memory.store import AssistantMemoryStore, new_entry_id, reset_memory_store


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
