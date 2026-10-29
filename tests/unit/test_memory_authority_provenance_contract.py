"""Obsidian RO-1 (#3506): authority/provenance contract tests.

Characterizes EXISTING concurrency/authority behavior ahead of any read-only
export work (#3507+). No synchronization or production-state change here;
every fixture is an isolated synthetic store or a mocked directory — no live
fleet data. See docs/operations/obsidian-ro1-authority-provenance.md for the
accompanying source/owner/scope matrix and go/no-go checklist.
"""

from __future__ import annotations

import os
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
        "entry_type": "decision",
        "author_principal": "principal:agent-a",
        "source_product": "dashboard",
        "tags": [],
        "body": {"summary": "contract-test fixture"},
        "created_at": "2026-10-29T00:00:00Z",
    }
    base.update(kwargs)
    return MemoryEntry(**base)  # type: ignore[arg-type]


def test_append_has_no_cas_on_concurrent_supersession(store: AssistantMemoryStore) -> None:
    """``append()``'s supersession link is a best-effort UPDATE, not a CAS.

    Two entries racing to supersede the same original both insert as active
    rows; only the original's own ``superseded_by`` column is exclusive
    (first writer wins there), but the second superseder is never told it
    lost — it is left indistinguishable from a successful, exclusive
    supersession. This is the generic-store gap the issue body calls out:
    "repeated/concurrent stale supersession ... where generic CAS/tombstones
    do NOT exist." Contrast with ``update_review()`` below, which IS a real
    CAS and raises ``stale_review_conflict`` on the losing writer.
    """
    original = _entry()
    store.append(original)

    racer_a = _entry(supersedes_id=original.id)
    racer_b = _entry(supersedes_id=original.id)
    store.append(racer_a)
    store.append(racer_b)

    refreshed_original = store.get(original.id)
    assert refreshed_original is not None
    assert refreshed_original.superseded_by == racer_a.id, "first writer wins the exclusive link"

    # Both racers believe they superseded `original`, and neither is marked
    # superseded itself: the store has no way to tell racer_b it lost.
    rows = store.list_entries(entry_type="decision", limit=10)
    active_ids = {r.id for r in rows if not r.superseded_by}
    assert active_ids == {racer_a.id, racer_b.id}, (
        "no CAS/tombstone on supersession: a losing racer silently remains active "
        "instead of being rejected or marked superseded"
    )


def test_update_review_is_a_real_cas_unlike_append(store: AssistantMemoryStore) -> None:
    """Contrast case: ``update_review()`` genuinely rejects a stale writer."""
    entry = _entry(review_status="proposed")
    store.append(entry)

    store.update_review(
        entry.id,
        expected_review_status="proposed",
        review_status="accepted",
        reviewed_by="librarian-a",
    )

    with pytest.raises(ValueError, match="stale_review_conflict"):
        store.update_review(
            entry.id,
            expected_review_status="proposed",
            review_status="quarantined",
            reviewed_by="librarian-b",
        )
    assert store.get(entry.id).review_status == "accepted"


def test_superseded_entries_are_retrievable_not_tombstoned(store: AssistantMemoryStore) -> None:
    """No tombstone concept exists: a superseded row stays fully readable."""
    original = _entry()
    store.append(original)
    successor = _entry(supersedes_id=original.id)
    store.append(successor)

    retrieved = store.get(original.id)
    assert retrieved is not None
    assert retrieved.body == original.body
    assert retrieved.superseded_by == successor.id


def test_idempotent_append_is_a_real_digest_cas(store: AssistantMemoryStore) -> None:
    """Contrast case: ``append_idempotent()`` IS atomic (BEGIN IMMEDIATE + key)."""
    first, replayed_first = store.append_idempotent(
        _entry(entry_type="session_digest"), idempotency_key="k1", fingerprint="fp1"
    )
    assert replayed_first is False

    second, replayed_second = store.append_idempotent(
        _entry(entry_type="session_digest"), idempotency_key="k1", fingerprint="fp1"
    )
    assert replayed_second is True
    assert second.id == first.id, "same key+fingerprint replays the original entry, no duplicate row"

    with pytest.raises(ValueError, match="idempotency_key_reused"):
        store.append_idempotent(_entry(entry_type="session_digest"), idempotency_key="k1", fingerprint="fp2")


def test_playbooks_store_has_no_database_only_an_mtime_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Correct a documentation assumption: #3505 calls it a "DB-cache", but
    the playbooks/manual store (#3486-#3490) has no database at all — it is
    a pure in-process cache keyed by directory + per-file mtime, rebuilt by
    re-reading YAML from disk. Confirmed here: changing a file's mtime without
    changing its content still invalidates and reloads the cache signature.
    """
    import staff.playbooks.store as store_mod

    monkeypatch.setenv(store_mod.PLAYBOOKS_DIR_ENV, str(tmp_path))
    store_mod.clear_playbooks_cache()
    try:
        manual = """\
schema_version: 1
version: "1.0.0"
title: Manual
sections:
  - anchor: a
    title: A
    body: body text
"""
        (tmp_path / "manual.yml").write_text(manual)
        first_sig = store_mod.load_manual()
        assert first_sig is not None

        before = store_mod._CACHE_SIG  # noqa: SLF001
        manual_path = tmp_path / "manual.yml"
        manual_path.write_text(manual)  # same bytes, deliberately bumped mtime below
        bumped = manual_path.stat().st_mtime + 5
        os.utime(manual_path, (bumped, bumped))
        store_mod.load_manual()
        after = store_mod._CACHE_SIG  # noqa: SLF001
        assert before != after, "cache signature is mtime-keyed, not content-hash-keyed or DB-backed"

        assert not any(tmp_path.glob("*.sqlite3")), (
            "playbooks store writes no database file, contrary to a 'DB-cache' assumption"
        )
    finally:
        store_mod.clear_playbooks_cache()


# NOTE: the lesson-review CAS (stale_review_conflict) and the "lessons cannot
# be superseded through generic append" boundary are already characterized by
# tests/unit/test_memory_lessons.py::test_review_rejects_a_stale_racing_status_transition
# and tests/unit/test_memory_lessons.py (append rejection assertion) respectively.
# Not duplicated here — see the matrix doc for the pointer.
