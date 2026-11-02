"""Unit tests for #3295 slice S4: persona:<role> fallback lease, slot ledger, and spool ingest."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from staff.barb_continuity import BarbContinuityStore
from staff.barb_lease_fence import StaleBarbLease
from staff.persona_fallback_lease import (
    acquire_persona_lease,
    append_fallback_spool,
    ingest_fallback_spool,
    is_scheduler_blocked_by_fallback,
    list_slot_ledger,
    mark_slot_complete,
    persona_scope,
    record_slot_claim,
    release_persona_lease,
    release_slot_claim,
    should_skip_slot,
    validate_persona_holder,
)


def test_persona_scope_and_holder_validation() -> None:
    assert persona_scope("bob") == "persona:bob"
    assert persona_scope("rd-barb") == "persona:rd-barb"
    with pytest.raises(ValueError, match="role"):
        persona_scope("")
    with pytest.raises(ValueError, match="role"):
        persona_scope("Bob_Invalid")

    assert validate_persona_holder("grok-fallback") == "grok-fallback"
    assert validate_persona_holder("rd:desk") == "rd:desk"
    with pytest.raises(ValueError, match="holder"):
        validate_persona_holder("unknown-holder")
    with pytest.raises(ValueError, match="holder"):
        validate_persona_holder("rd:")


def test_persona_lease_checkout_fencing_and_scheduler_skip(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "continuity.sqlite3")
    t0 = datetime(2026, 10, 28, 17, 0, tzinfo=UTC)

    assert is_scheduler_blocked_by_fallback(store, "bob", now=t0) is False
    res1 = acquire_persona_lease(store, "bob", holder="grok-fallback", summary="Bob took over", now=t0)
    assert res1["acquired"] is True
    assert res1["lease"]["scope"] == "persona:bob"
    assert res1["lease"]["epoch"] == 1
    assert is_scheduler_blocked_by_fallback(store, "bob", now=t0) is True
    assert should_skip_slot(store, role="bob", job="daily-note", slot="2026-10-28T17:00", now=t0) is True

    conflict = acquire_persona_lease(store, "bob", holder="rd:desk", force=False, now=t0)
    assert conflict["acquired"] is False
    assert conflict["conflict"] is True

    with pytest.raises(StaleBarbLease):
        release_persona_lease(store, "bob", holder="grok-fallback", epoch=99, now=t0)
    with pytest.raises(StaleBarbLease):
        release_persona_lease(store, "bob", holder="rd:desk", epoch=1, now=t0)

    rel = release_persona_lease(store, "bob", holder="grok-fallback", epoch=1, summary="Bob is back", now=t0)
    assert rel["released"] is True
    assert is_scheduler_blocked_by_fallback(store, "bob", now=t0) is False

    res2 = acquire_persona_lease(store, "bob", holder="rd:desk", summary="RD resumed", now=t0)
    assert res2["acquired"] is True
    assert res2["lease"]["epoch"] == 2


def test_slot_ledger_idempotency_prevents_duplicate_slot_after_resume(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "continuity.sqlite3")
    t0 = datetime(2026, 10, 28, 17, 0, tzinfo=UTC)

    first = record_slot_claim(
        store, role="bob", job="daily-note", slot="2026-10-28T17:00", holder="grok-fallback", epoch=1, now=t0
    )
    assert first["recorded"] is True
    assert first["duplicate"] is False
    assert first["key"] == "bob/daily-note/2026-10-28T17:00"

    dup = record_slot_claim(
        store, role="bob", job="daily-note", slot="2026-10-28T17:00", holder="rd:desk", epoch=2, now=t0
    )
    assert dup["recorded"] is False
    assert dup["duplicate"] is True
    assert dup["holder"] == "grok-fallback"
    assert dup["epoch"] == 1

    assert should_skip_slot(store, role="bob", job="daily-note", slot="2026-10-28T17:00", now=t0) is True
    assert should_skip_slot(store, role="bob", job="daily-note", slot="2026-10-29T17:00", now=t0) is False
    rows = list_slot_ledger(store, role="bob")
    assert len(rows) == 1
    assert rows[0]["key"] == "bob/daily-note/2026-10-28T17:00"


def test_offline_fallback_spool_append_and_ingest(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "continuity.sqlite3")
    spool_path = tmp_path / "fallback-spool.jsonl"
    t0 = datetime(2026, 10, 28, 17, 5, tzinfo=UTC)

    entry = append_fallback_spool(
        spool_path, role="bob", job="daily-note", slot="2026-10-28T17:00", epoch=3, board_claim_ref="RM#2376", now=t0
    )
    assert entry["key"] == "bob/daily-note/2026-10-28T17:00"

    result = ingest_fallback_spool(store, spool_path, now=t0)
    assert result == {"ingested": 1, "duplicates": 0, "keys": ["bob/daily-note/2026-10-28T17:00"]}
    assert spool_path.read_text(encoding="utf-8") == ""
    assert should_skip_slot(store, role="bob", job="daily-note", slot="2026-10-28T17:00", now=t0) is True

    rd_lease = acquire_persona_lease(store, "bob", holder="rd:desk", summary="resume after spool", now=t0)
    assert rd_lease["lease"]["epoch"] == 4
    assert ingest_fallback_spool(store, spool_path, now=t0) == {"ingested": 0, "duplicates": 0, "keys": []}


def test_malformed_spool_and_sqlite_error_fail_closed(tmp_path: Path) -> None:
    store = BarbContinuityStore(tmp_path / "continuity.sqlite3")
    spool_path = tmp_path / "bad-spool.jsonl"
    spool_path.write_text('{"role": "bob", "job": "daily-note"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="spool"):
        ingest_fallback_spool(store, spool_path)
    assert spool_path.read_text(encoding="utf-8") != ""

    store._conn.close()
    assert is_scheduler_blocked_by_fallback(store, "bob") is True
    assert should_skip_slot(store, role="bob", job="daily-note", slot="2026-10-28T17:00") is True


def test_failed_attempt_releases_claim_and_retry_succeeds(tmp_path: Path) -> None:
    """#3789: a claim that is never completed (the scheduled work failed) must not permanently
    block the slot -- the old behavior treated any ledger row as "already executed" forever."""
    store = BarbContinuityStore(tmp_path / "continuity.sqlite3")
    t0 = datetime(2026, 10, 28, 17, 0, tzinfo=UTC)

    claim = record_slot_claim(
        store, role="bob", job="daily-note", slot="2026-10-28T17:00", holder="rd:desk", epoch=1, now=t0
    )
    assert claim["recorded"] is True and claim["status"] == "in_progress"
    # Work is in progress: a concurrent attempt must not also execute.
    concurrent = record_slot_claim(
        store, role="bob", job="daily-note", slot="2026-10-28T17:00", holder="rd:desk", epoch=1, now=t0
    )
    assert concurrent["recorded"] is False and concurrent["duplicate"] is True

    released = release_slot_claim(
        store, role="bob", job="daily-note", slot="2026-10-28T17:00", holder="rd:desk", epoch=1, now=t0
    )
    assert released["released"] is True
    assert should_skip_slot(store, role="bob", job="daily-note", slot="2026-10-28T17:00", now=t0) is False

    retry = record_slot_claim(
        store, role="bob", job="daily-note", slot="2026-10-28T17:00", holder="rd:desk", epoch=1, now=t0
    )
    assert retry["recorded"] is True
    done = mark_slot_complete(
        store, role="bob", job="daily-note", slot="2026-10-28T17:00", holder="rd:desk", epoch=1, now=t0
    )
    assert done["completed"] is True and done["status"] == "completed"
    assert should_skip_slot(store, role="bob", job="daily-note", slot="2026-10-28T17:00", now=t0) is True

    # A completed slot is a genuine duplicate forever -- no further claim is granted.
    replay = record_slot_claim(
        store, role="bob", job="daily-note", slot="2026-10-28T17:00", holder="rd:desk", epoch=1, now=t0
    )
    assert replay["recorded"] is False and replay["duplicate"] is True and replay["status"] == "completed"


def test_stale_holder_epoch_cannot_complete_or_release_another_claim(tmp_path: Path) -> None:
    """#3789: a holder/epoch that no longer owns the in-progress claim (it was released and
    reclaimed by someone else) must not be able to complete or release that newer claim."""
    store = BarbContinuityStore(tmp_path / "continuity.sqlite3")
    t0 = datetime(2026, 10, 28, 17, 0, tzinfo=UTC)

    record_slot_claim(
        store, role="bob", job="daily-note", slot="2026-10-28T17:00", holder="grok-fallback", epoch=1, now=t0
    )
    release_slot_claim(
        store, role="bob", job="daily-note", slot="2026-10-28T17:00", holder="grok-fallback", epoch=1, now=t0
    )
    record_slot_claim(store, role="bob", job="daily-note", slot="2026-10-28T17:00", holder="rd:desk", epoch=2, now=t0)

    stale_complete = mark_slot_complete(
        store, role="bob", job="daily-note", slot="2026-10-28T17:00", holder="grok-fallback", epoch=1, now=t0
    )
    assert stale_complete == {
        "completed": False,
        "reason": "stale_or_missing_claim",
        "key": "bob/daily-note/2026-10-28T17:00",
    }
    stale_release = release_slot_claim(
        store, role="bob", job="daily-note", slot="2026-10-28T17:00", holder="grok-fallback", epoch=1, now=t0
    )
    assert stale_release == {
        "released": False,
        "reason": "stale_or_missing_claim",
        "key": "bob/daily-note/2026-10-28T17:00",
    }
    # The real (newer) claimant can still complete it.
    assert should_skip_slot(store, role="bob", job="daily-note", slot="2026-10-28T17:00", now=t0) is True
    real_complete = mark_slot_complete(
        store, role="bob", job="daily-note", slot="2026-10-28T17:00", holder="rd:desk", epoch=2, now=t0
    )
    assert real_complete["completed"] is True


def test_legacy_ledger_row_without_status_column_stays_skipped(tmp_path: Path) -> None:
    """#3789: a ledger row written before this migration has no recorded status. Migrating the
    column must default it to "completed" -- the safe treatment, since the old code already
    treated any row's mere existence as "never retry", and we must not silently start retrying
    old slots that genuinely already ran."""
    store = BarbContinuityStore(tmp_path / "continuity.sqlite3")
    with store._lock, store._conn:
        store._conn.executescript(
            "CREATE TABLE persona_slot_ledger (slot_key TEXT PRIMARY KEY, role TEXT NOT NULL, job TEXT NOT NULL, "
            "slot TEXT NOT NULL, holder TEXT NOT NULL, epoch INTEGER NOT NULL, "
            "board_claim_ref TEXT NOT NULL DEFAULT '', recorded_at TEXT NOT NULL);"
        )
        store._conn.execute(
            "INSERT INTO persona_slot_ledger (slot_key, role, job, slot, holder, epoch, recorded_at) "
            "VALUES ('bob/daily-note/2026-10-28T17:00', 'bob', 'daily-note', '2026-10-28T17:00', 'rd:desk', 1, "
            "'2026-10-28T17:00:00+00:00')"
        )

    assert should_skip_slot(store, role="bob", job="daily-note", slot="2026-10-28T17:00") is True
    rows = list_slot_ledger(store, role="bob")
    assert rows[0]["status"] == "completed"
