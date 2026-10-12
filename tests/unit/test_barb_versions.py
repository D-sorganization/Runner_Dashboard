"""Version-gated, idempotent run-card upsert at the Barb authority (#1800, BR-06).

Invariants under test (design by contract):
  I2  an ``(run_id, version)`` is applied at most once;
  I3  the stored version never decreases and a stale version is rejected;
  I1  an applied write has a durable receipt that survives a store reopen.
"""

from __future__ import annotations

import itertools
import random
from pathlib import Path

import pytest
from staff.barb_versions import ApplyOutcome, StaleEpochError
from staff.conversations import ConversationStore
from staff.run_link import post_run_card, run_card_id
from staff.store import RunRecord, RunStore


@pytest.fixture
def store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(tmp_path / "authority.sqlite3")


@pytest.fixture
def thread_id(store: ConversationStore) -> str:
    return store.create_thread(title="t", kind="direct", role="librarian", created_by="user:d").id


def _apply(store: ConversationStore, thread_id: str, version: int | None, status: str, **kw):  # type: ignore[no-untyped-def]
    return store.apply_run_card_update(
        thread_id=thread_id,
        run_id="run-1",
        message_id=run_card_id("run-1"),
        status=status,
        version=version,
        body_md=f"card {status} v{version}",
        meta={"run_id": "run-1", "status": status},
        author="librarian",
        **kw,
    )


@pytest.mark.unit
def test_migrations_4_to_6_are_applied_and_additive(store: ConversationStore) -> None:
    names = {r["name"] for r in store._conn.execute("SELECT name FROM schema_migrations").fetchall()}
    assert {"barb_outbox", "run_card_versions", "barb_receipts"} <= names
    tables = {r["name"] for r in store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"barb_outbox", "run_card_versions", "barb_receipts"} <= tables
    # Pre-existing tables are untouched (reversible-safe: old code ignores the new ones).
    assert {"threads", "messages", "action_proposals"} <= tables


@pytest.mark.unit
def test_runs_gets_card_version_column_and_monotonic_counter(tmp_path: Path) -> None:
    runs = RunStore(tmp_path / "authority.sqlite3")
    assert "card_version" in runs.columns()
    runs.create_run(
        RunRecord(
            id="run-1",
            role="librarian",
            provider="claude",
            model=None,
            machine="n",
            repo="r",
            target_kind="prompt",
            target_ref="",
            prompt="p",
        )
    )
    assert [runs.next_card_version("run-1") for _ in range(3)] == [1, 2, 3]
    with pytest.raises(KeyError):
        runs.next_card_version("missing")


@pytest.mark.unit
def test_applied_duplicate_stale(store: ConversationStore, thread_id: str) -> None:
    msg, first = _apply(store, thread_id, 1, "running")
    assert first.outcome is ApplyOutcome.APPLIED and first.version == 1 and first.receipt_id
    assert msg is not None and msg.meta["status"] == "running"

    _, dup = _apply(store, thread_id, 1, "running")
    assert dup.outcome is ApplyOutcome.DUPLICATE
    assert dup.receipt_id == first.receipt_id  # the original receipt is returned

    _, newer = _apply(store, thread_id, 3, "running")
    assert newer.outcome is ApplyOutcome.APPLIED and newer.version == 3

    _, stale = _apply(store, thread_id, 2, "running")
    assert stale.outcome is ApplyOutcome.STALE and stale.version == 3  # stale carries the current version
    assert len(store.list_messages(thread_id)) == 1  # never accepted twice


@pytest.mark.unit
def test_terminal_is_sticky_even_against_a_higher_non_terminal_version(
    store: ConversationStore, thread_id: str
) -> None:
    _apply(store, thread_id, 2, "succeeded")
    msg, res = _apply(store, thread_id, 9, "running")
    assert res.outcome is ApplyOutcome.STALE
    assert msg is not None and msg.meta["status"] == "succeeded"
    # A retry attempt that reaches a higher-version terminal may replace the terminal.
    _, retry = _apply(store, thread_id, 3, "failed")
    assert retry.outcome is ApplyOutcome.APPLIED
    # But an equal or lower terminal cannot.
    _, low = _apply(store, thread_id, 3, "succeeded")
    assert low.outcome is ApplyOutcome.DUPLICATE
    _, lower = _apply(store, thread_id, 1, "succeeded")
    assert lower.outcome is ApplyOutcome.STALE


@pytest.mark.unit
def test_legacy_terminal_card_without_version_row_is_sticky(store: ConversationStore, thread_id: str) -> None:
    """A card written before migration 5 has a status but no version row; it must not regress."""
    store.add_message(
        thread_id=thread_id,
        author_kind="role",
        author="librarian",
        kind="run_card",
        body_md="old",
        meta={"status": "succeeded"},
        run_id="run-1",
        message_id=run_card_id("run-1"),
    )
    _, res = _apply(store, thread_id, 1, "running")
    assert res.outcome is ApplyOutcome.STALE


@pytest.mark.unit
@pytest.mark.parametrize("seed", range(20))
def test_any_delivery_order_converges_to_max_version(store: ConversationStore, thread_id: str, seed: int) -> None:
    """Property: duplicated/reordered deliveries end at the highest version, version never decreases."""
    statuses = {1: "queued", 2: "running", 3: "running", 4: "succeeded"}
    deliveries = list(itertools.chain.from_iterable([(v, s)] * 2 for v, s in statuses.items()))
    random.Random(seed).shuffle(deliveries)
    seen = 0
    for version, status in deliveries:
        _, res = _apply(store, thread_id, version, status)
        assert res.version >= seen  # I3: never decreases
        seen = res.version
    msg = store.get_message(run_card_id("run-1"))
    assert msg is not None and msg.meta["status"] == "succeeded"
    row = store._conn.execute("SELECT version FROM run_card_versions WHERE run_id='run-1'").fetchone()
    assert row["version"] == 4
    assert len(store.list_messages(thread_id)) == 1
    receipts = store._conn.execute("SELECT COUNT(*) AS n FROM barb_receipts").fetchone()["n"]
    assert receipts <= 4  # at most one receipt per applied version (I2)


@pytest.mark.unit
def test_applied_write_is_durable_across_reopen(tmp_path: Path) -> None:
    """I1: once applied (acknowledged), the card and receipt survive a crash-style reopen."""
    path = tmp_path / "d.sqlite3"
    s1 = ConversationStore(path)
    tid = s1.create_thread(title="t", kind="direct", role="librarian", created_by="user:d").id
    _, res = _apply(s1, tid, 1, "running")
    synchronous = s1._conn.execute("PRAGMA synchronous").fetchone()[0]
    assert synchronous == 2  # FULL
    s1._conn.close()  # no checkpoint: simulates the process dying after the ack
    s2 = ConversationStore(path)
    assert s2.get_message(run_card_id("run-1")) is not None
    row = s2._conn.execute("SELECT receipt_id FROM barb_receipts WHERE ref_id='run-1:1'").fetchone()
    assert row["receipt_id"] == res.receipt_id


@pytest.mark.unit
def test_failure_between_gate_and_commit_rolls_back_atomically(store: ConversationStore, thread_id: str) -> None:
    """Message write, version row and receipt commit together or not at all."""
    original = store._conn.execute
    calls = {"n": 0}

    class Boom(RuntimeError):
        pass

    class FailingConn:
        def __init__(self, inner):  # type: ignore[no-untyped-def]
            self._inner = inner

        def execute(self, sql, *a):  # type: ignore[no-untyped-def]
            if sql.lstrip().startswith("INSERT INTO barb_receipts"):
                calls["n"] += 1
                raise Boom("disk full")
            return self._inner.execute(sql, *a)

        def __getattr__(self, name):  # type: ignore[no-untyped-def]
            return getattr(self._inner, name)

    real = store._conn
    store._conn = FailingConn(real)  # type: ignore[assignment]
    with pytest.raises(Boom):
        _apply(store, thread_id, 1, "running")
    store._conn = real
    assert calls["n"] == 1 and original is not None
    assert store.list_messages(thread_id) == []
    assert real.execute("SELECT COUNT(*) AS n FROM run_card_versions").fetchone()["n"] == 0


@pytest.mark.unit
def test_epoch_mismatch_is_rejected_before_any_write(store: ConversationStore, thread_id: str) -> None:
    with pytest.raises(StaleEpochError):
        _apply(store, thread_id, 1, "running", epoch=1, authority_epoch=2)
    with pytest.raises(StaleEpochError):
        _apply(store, thread_id, 1, "running", epoch=3, authority_epoch=2)  # a stale authority is fenced too
    assert store.list_messages(thread_id) == []
    _, ok = _apply(store, thread_id, 1, "running", epoch=2, authority_epoch=2)
    assert ok.outcome is ApplyOutcome.APPLIED


@pytest.mark.unit
def test_invalid_version_is_a_precondition_violation(store: ConversationStore, thread_id: str) -> None:
    with pytest.raises(ValueError):
        _apply(store, thread_id, 0, "running")
    with pytest.raises(ValueError):
        _apply(store, thread_id, 1, "bogus-status")


@pytest.mark.unit
def test_post_run_card_uses_the_gate_and_never_regresses_a_terminal(store: ConversationStore, thread_id: str) -> None:
    run = RunRecord(
        id="run-1",
        role="librarian",
        provider="claude",
        model=None,
        machine="n",
        repo="r",
        target_kind="prompt",
        target_ref="",
        prompt="p",
        thread_id=thread_id,
    )

    class Bus:
        def __init__(self) -> None:
            self.events = 0

        def publish_message_sync(self, *_a: object) -> None:
            self.events += 1

    bus = Bus()
    post_run_card(run, "running", store=store, bus=bus)
    post_run_card(run, "succeeded", store=store, bus=bus)
    late = post_run_card(run, "running", store=store, bus=bus)
    assert late is not None and late.meta["status"] == "succeeded"
    assert bus.events == 2  # the rejected late update is not published
    assert len(store.list_messages(thread_id)) == 1
