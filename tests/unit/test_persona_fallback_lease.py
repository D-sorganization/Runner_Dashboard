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
    persona_scope,
    record_slot_claim,
    release_persona_lease,
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
