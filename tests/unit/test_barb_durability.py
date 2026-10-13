"""BR-06 slice 4 durability hardening (#1800): sync=FULL everywhere, typed config errors,
bounded receipt pruning that keeps dedupe/stale-rejection intact, explicit outbox payload conflicts."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from staff.audit import StaffAuditStore
from staff.barb_db import BarbConfigError
from staff.barb_outbox import OutboxConflictError, bounds_from_env
from staff.barb_versions import ApplyOutcome, receipt_limits_from_env
from staff.conversations import ConversationStore
from staff.run_link import run_card_id
from staff.store import RunStore

FULL = 2


@pytest.mark.unit
def test_run_and_audit_stores_use_synchronous_full(tmp_path: Path) -> None:
    assert RunStore(tmp_path / "runs.sqlite3")._conn.execute("PRAGMA synchronous").fetchone()[0] == FULL
    assert StaffAuditStore(tmp_path / "audit.sqlite3")._conn.execute("PRAGMA synchronous").fetchone()[0] == FULL


@pytest.mark.unit
@pytest.mark.parametrize("raw", ["abc", "1.5", "0", "-3"])
def test_bounds_from_env_rejects_malformed_with_typed_error(raw: str) -> None:
    with pytest.raises(BarbConfigError, match="BARB_OUTBOX_MAX_BYTES"):
        bounds_from_env({"BARB_OUTBOX_MAX_BYTES": raw})


@pytest.mark.unit
def test_bounds_from_env_defaults_and_valid_values() -> None:
    assert bounds_from_env({"BARB_OUTBOX_MAX_ENTRIES": "7"})[0] == 7
    assert bounds_from_env({})[0] == 5000


@pytest.mark.unit
def test_receipt_limits_from_env_typed_error() -> None:
    with pytest.raises(BarbConfigError, match="BARB_RECEIPT_MAX_ROWS"):
        receipt_limits_from_env({"BARB_RECEIPT_MAX_ROWS": "lots"})
    assert receipt_limits_from_env({"BARB_RECEIPT_MAX_ROWS": "9", "BARB_RECEIPT_MAX_AGE_DAYS": "2"}) == (9, 2 * 86400.0)


def _apply(store: ConversationStore, tid: str, run: str, version: int, status: str):  # type: ignore[no-untyped-def]
    return store.apply_run_card_update(
        thread_id=tid,
        run_id=run,
        message_id=run_card_id(run),
        status=status,
        version=version,
        body_md=f"{run} v{version}",
        meta={"run_id": run, "status": status},
        author="role:librarian",
    )


def _receipts(store: ConversationStore) -> set[str]:
    return {r["ref_id"] for r in store._conn.execute("SELECT ref_id FROM barb_receipts").fetchall()}


@pytest.fixture
def store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(tmp_path / "a.sqlite3")


@pytest.fixture
def tid(store: ConversationStore) -> str:
    return store.create_thread(title="t", kind="direct", role="librarian", created_by="user:d").id


@pytest.mark.unit
def test_prune_removes_old_superseded_receipts_but_keeps_head(store: ConversationStore, tid: str) -> None:
    for v, s in [(1, "queued"), (2, "running"), (3, "running")]:
        _apply(store, tid, "run-1", v, s)
    assert _receipts(store) == {"run-1:1", "run-1:2", "run-1:3"}
    removed = store.prune_barb_receipts(max_rows=1000, max_age_seconds=0.0)
    assert removed == 2
    assert _receipts(store) == {"run-1:3"}  # the head receipt is what dedupe still needs


@pytest.mark.unit
def test_dedupe_and_stale_rejection_hold_after_pruning(store: ConversationStore, tid: str) -> None:
    receipts = {}
    for v, s in [(1, "queued"), (2, "running"), (3, "running")]:
        _, res = _apply(store, tid, "run-1", v, s)
        receipts[v] = res.receipt_id
    store.prune_barb_receipts(max_rows=1000, max_age_seconds=0.0)
    # replay of the newest version: duplicate, same receipt id, nothing re-applied
    msg, dup = _apply(store, tid, "run-1", 3, "running")
    assert dup.outcome is ApplyOutcome.DUPLICATE and dup.receipt_id == receipts[3]
    # replays of pruned older versions are rejected as stale and never re-applied
    for v in (1, 2):
        _, stale = _apply(store, tid, "run-1", v, "queued")
        assert stale.outcome is ApplyOutcome.STALE and stale.version == 3
    assert _receipts(store) == {"run-1:3"}
    assert msg is not None and len(store.list_messages(tid)) == 1
    _, nxt = _apply(store, tid, "run-1", 4, "succeeded")
    assert nxt.outcome is ApplyOutcome.APPLIED


@pytest.mark.unit
def test_prune_respects_age_and_row_cap(store: ConversationStore, tid: str) -> None:
    for run in ("a", "b", "c", "d"):
        _apply(store, tid, run, 1, "queued")
        _apply(store, tid, run, 2, "running")
    # fresh receipts + generous limits: nothing removed
    assert store.prune_barb_receipts(max_rows=1000, max_age_seconds=3600.0) == 0
    # count cap: keep at most 2 superseded receipts (heads are always kept)
    removed = store.prune_barb_receipts(max_rows=2, max_age_seconds=3600.0)
    assert removed == 2
    refs = _receipts(store)
    assert {f"{r}:2" for r in "abcd"} <= refs and len(refs) == 6


@pytest.mark.unit
def test_prune_batch_is_bounded(store: ConversationStore, tid: str) -> None:
    for v in range(1, 8):
        _apply(store, tid, "run-1", v, "running")
    assert store.prune_barb_receipts(max_rows=0, max_age_seconds=0.0, batch=3) == 3
    assert len(_receipts(store)) == 4


@pytest.mark.unit
def test_apply_prunes_automatically_when_over_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BARB_RECEIPT_MAX_ROWS", "3")
    monkeypatch.setenv("BARB_RECEIPT_MAX_AGE_DAYS", "0")
    monkeypatch.setattr("staff.barb_store.RECEIPT_PRUNE_EVERY", 1)
    s = ConversationStore(tmp_path / "auto.sqlite3")
    t = s.create_thread(title="t", kind="direct", role="librarian", created_by="user:d").id
    for v in range(1, 12):
        _apply(s, t, "run-1", v, "running")
    refs = _receipts(s)
    assert "run-1:11" in refs and len(refs) <= 4
    _, dup = _apply(s, t, "run-1", 11, "running")
    assert dup.outcome is ApplyOutcome.DUPLICATE


@pytest.mark.unit
def test_enqueue_same_key_different_payload_is_an_explicit_error(tmp_path: Path) -> None:
    cs = ConversationStore(tmp_path / "n.sqlite3")
    box = cs.barb_outbox(max_entries=10, max_bytes=100_000, max_entry_bytes=10_000, rng=random.Random(1))
    first = box.enqueue("run_card", "thr", "r", 1, {"status": "running"})
    assert box.enqueue("run_card", "thr", "r", 1, {"status": "running"}) == first  # identical replay: idempotent
    with pytest.raises(OutboxConflictError):
        box.enqueue("run_card", "thr", "r", 1, {"status": "failed"})
    assert box.depth() == 1
