"""Obsidian RO-1 (#3506): authority/provenance contract tests.

Characterizes EXISTING concurrency/authority behavior ahead of any read-only
export work (#3507+). No synchronization or production-state change here;
every fixture is an isolated synthetic store or a mocked directory — no live
fleet data. See docs/operations/obsidian-ro1-authority-provenance.md for the
accompanying source/owner/scope matrix and go/no-go checklist.
"""

from __future__ import annotations

import datetime as _dt_mod
import enum as _enum_mod
import os
import sys
import typing as _typing_mod
from pathlib import Path

import pytest

if not hasattr(_dt_mod, "UTC"):
    _dt_mod.UTC = _dt_mod.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017
if not hasattr(_typing_mod, "Self"):
    from typing import Self as _SelfFallback

    _typing_mod.Self = _SelfFallback  # type: ignore[attr-defined]
if not hasattr(_enum_mod, "StrEnum"):

    class _StrEnum(str, _enum_mod.Enum):  # noqa: UP042
        pass

    _enum_mod.StrEnum = _StrEnum  # type: ignore[attr-defined,misc]

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from memory.models import MemoryEntry  # noqa: E402
from memory.store import AssistantMemoryStore, new_entry_id, reset_memory_store  # noqa: E402

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


def test_append_enforces_cas_on_concurrent_supersession(store: AssistantMemoryStore, tmp_path: Path) -> None:
    """``append()``'s supersession link is an atomic CAS (#3546).

    When multiple writers race to supersede the same original entry, exactly
    one writer wins and becomes the sole current successor; losing writers
    raise ``stale_supersession_conflict`` and their rows are rolled back.
    """
    from concurrent.futures import ThreadPoolExecutor

    original = _entry()
    store.append(original)

    racer_a = _entry(supersedes_id=original.id)
    racer_b = _entry(supersedes_id=original.id)
    store.append(racer_a)
    with pytest.raises(ValueError, match="stale_supersession_conflict"):
        store.append(racer_b)

    refreshed_original = store.get(original.id)
    assert refreshed_original is not None
    assert refreshed_original.superseded_by == racer_a.id
    assert store.get(racer_b.id) is None

    # Concurrent multi-connection race: 8 writers against the same target -> 1 wins, 7 conflict.
    concurrent_target = _entry()
    store.append(concurrent_target)
    stores = [AssistantMemoryStore(tmp_path / "mem.sqlite3") for _ in range(8)]

    def _race(idx: int) -> str:
        try:
            won = stores[idx].append(_entry(supersedes_id=concurrent_target.id))
            return won.id
        except ValueError as exc:
            assert str(exc) == "stale_supersession_conflict"
            return "conflict"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(_race, range(8)))

    winners = [o for o in outcomes if o != "conflict"]
    assert len(winners) == 1 and outcomes.count("conflict") == 7
    refreshed_target = store.get(concurrent_target.id)
    assert refreshed_target is not None and refreshed_target.superseded_by == winners[0]
    rows = store.list_entries(entry_type="decision", limit=20)
    active_ids = {r.id for r in rows if not r.superseded_by}
    assert active_ids == {racer_a.id, winners[0]}

    # Also verify service-layer append_entry translates stale_supersession_conflict to HTTP 409.
    from fastapi import HTTPException
    from identity import Principal
    from memory.models import MemoryAppendRequest
    from memory.service import append_entry

    writer = Principal(id="agent-a", type="bot", name="A", scopes=["memory.write"])
    with pytest.raises(HTTPException) as exc:
        append_entry(
            writer,
            MemoryAppendRequest(
                entry_type="decision",
                source_product="dashboard",
                body={"summary": "stale successor via service"},
                supersedes_id=original.id,
            ),
        )
    assert exc.value.status_code == 409
    assert "already superseded" in str(exc.value.detail)


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
