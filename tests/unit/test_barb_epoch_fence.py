"""BR-06 final slice: the persisted epoch fence (#1800, ADR 0008 invariant I4).

Before this slice the authority epoch came only from configuration, so a restarted old authority (or a
restored backup with a rolled-back ``BARB_AUTHORITY_EPOCH``) was not refused by stored state. The fence is
a monotonic high-water mark ``(epoch, node)`` persisted in the conversation database:

* a configured epoch below the fence is refused (a demoted authority stays demoted across restarts);
* changing the authority node without bumping the epoch is refused (a node change is explicit);
* a higher epoch advances the fence in the same transaction as the write;
* a refused write commits nothing, and a refused local card is staged in the outbox, not lost.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from staff import barb_runtime
from staff.barb_fence import EpochFence, FencedAuthorityError, check_fence
from staff.barb_versions import ApplyOutcome, ApplyResult, StaleEpochError
from staff.conversation_migrations import backup_database
from staff.conversations import ConversationStore
from staff.run_link import handle_run_status_change, run_card_id
from staff.store import RunRecord, RunStore


def _apply(
    store: ConversationStore, thread_id: str, version: int, *, epoch: int, node: str, status: str = "running"
) -> ApplyResult:
    _, result = store.apply_run_card_update(
        thread_id=thread_id,
        run_id="run-1",
        message_id=run_card_id("run-1"),
        status=status,
        version=version,
        body_md=f"card v{version}",
        meta={"run_id": "run-1", "status": status},
        author="librarian",
        authority_epoch=epoch,
        authority_node=node,
    )
    return result


@pytest.fixture
def store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(tmp_path / "authority.sqlite3")


def _thread(store: ConversationStore) -> str:
    return store.create_thread(title="t", kind="direct", role="librarian", created_by="user:d").id


# ── pure rule ────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_check_fence_rules() -> None:
    fence = EpochFence(epoch=3, node="CT", reason="", updated_at="x")
    check_fence(None, 1, "CT")  # no fence yet: anything >= 1 is fine
    check_fence(fence, 3, "ct")  # same epoch, same node (case-insensitive)
    check_fence(fence, 4, "DESK")  # a higher epoch may name a new node
    with pytest.raises(FencedAuthorityError, match="epoch 2 is below the fence 3"):
        check_fence(fence, 2, "CT")
    with pytest.raises(FencedAuthorityError, match="node change"):
        check_fence(fence, 3, "DESK")
    with pytest.raises(ValueError):
        check_fence(fence, 0, "CT")
    assert issubclass(FencedAuthorityError, StaleEpochError)  # maps to 409 stale_authority_epoch


# ── store: persisted, transactional ──────────────────────────────────────────
@pytest.mark.unit
def test_an_applied_write_records_the_fence(store: ConversationStore) -> None:
    assert store.barb_epoch_fence() is None
    assert _apply(store, _thread(store), 1, epoch=2, node="CT").outcome is ApplyOutcome.APPLIED
    fence = store.barb_epoch_fence()
    assert fence is not None and (fence.epoch, fence.node) == (2, "CT")


@pytest.mark.unit
def test_a_restarted_old_authority_is_refused_by_stored_state(tmp_path: Path) -> None:
    path = tmp_path / "authority.sqlite3"
    old = ConversationStore(path)
    tid = _thread(old)
    _apply(old, tid, 1, epoch=1, node="CT")
    old.advance_barb_epoch_fence(2, "DESK", reason="failover")  # the operator fences the old authority
    old.close()

    restarted = ConversationStore(path)  # stale configuration still says epoch 1, node CT
    with pytest.raises(FencedAuthorityError):
        _apply(restarted, tid, 2, epoch=1, node="CT", status="succeeded")
    assert restarted.get_message(run_card_id("run-1")).meta["status"] == "running"  # type: ignore[union-attr]
    fence = restarted.barb_epoch_fence()
    assert fence is not None and (fence.epoch, fence.node, fence.reason) == (2, "DESK", "failover")


@pytest.mark.unit
def test_a_node_change_without_an_epoch_bump_is_refused(store: ConversationStore) -> None:
    tid = _thread(store)
    _apply(store, tid, 1, epoch=4, node="CT")
    with pytest.raises(FencedAuthorityError, match="node change"):
        _apply(store, tid, 2, epoch=4, node="DESK")
    assert _apply(store, tid, 2, epoch=5, node="DESK").outcome is ApplyOutcome.APPLIED


@pytest.mark.unit
def test_the_fence_never_moves_backwards(store: ConversationStore) -> None:
    store.advance_barb_epoch_fence(3, "CT")
    with pytest.raises(FencedAuthorityError):
        store.advance_barb_epoch_fence(2, "CT")
    same = store.advance_barb_epoch_fence(3, "CT")  # idempotent
    assert (same.epoch, same.node) == (3, "CT")


@pytest.mark.unit
def test_an_unfenced_legacy_write_is_unchanged(store: ConversationStore) -> None:
    """authority_epoch 0 (UNSET role) never reads or writes the fence."""
    tid = _thread(store)
    store.advance_barb_epoch_fence(9, "CT")
    assert _apply(store, tid, 1, epoch=0, node="").outcome is ApplyOutcome.APPLIED


@pytest.mark.unit
def test_restore_then_promote_needs_an_epoch_bump(store: ConversationStore, tmp_path: Path) -> None:
    """Restoring the authority onto a new node keeps IDs and receipts and makes the node change explicit."""
    tid = _thread(store)
    applied = _apply(store, tid, 1, epoch=1, node="CT")
    backup = backup_database(store.path)
    assert backup is not None
    target = tmp_path / "desk" / "authority.sqlite3"
    target.parent.mkdir()
    shutil.copyfile(backup, target)
    desk = ConversationStore(target)
    with pytest.raises(FencedAuthorityError):
        _apply(desk, tid, 2, epoch=1, node="DESK")  # forgot to bump the epoch
    replay = _apply(desk, tid, 1, epoch=2, node="DESK")
    assert replay.outcome is ApplyOutcome.DUPLICATE and replay.receipt_id == applied.receipt_id
    fence = desk.barb_epoch_fence()
    assert fence is not None and (fence.epoch, fence.node) == (2, "DESK")


# ── runtime: a fenced authority never writes and never loses a card ──────────
@pytest.fixture
def fenced_ct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    conv = ConversationStore(tmp_path / "ct.sqlite3")
    runs = RunStore(tmp_path / "runs.sqlite3")
    tid = _thread(conv)
    conv.advance_barb_epoch_fence(2, "DESK", reason="failover")
    monkeypatch.setenv("BARB_AUTHORITY_NODE", "CT")
    monkeypatch.setenv("BARB_AUTHORITY_URL", "http://ct:8321")
    monkeypatch.setenv("BARB_AUTHORITY_EPOCH", "1")
    monkeypatch.setattr(barb_runtime, "local_node_name", lambda: "CT")
    monkeypatch.setattr(barb_runtime, "get_conversation_store", lambda: conv)
    monkeypatch.setattr(barb_runtime, "get_store", lambda: runs)
    monkeypatch.setattr("staff.run_link.get_conversation_store", lambda: conv)
    barb_runtime.reset_authority_config()
    yield {"conv": conv, "runs": runs, "thread": tid}
    barb_runtime.reset_authority_config()


def _run(thread_id: str) -> RunRecord:
    return RunRecord(
        id="run-1",
        role="librarian",
        provider="claude",
        model=None,
        machine="CT",
        repo="Repo",
        target_kind="prompt",
        target_ref="",
        prompt="p",
        thread_id=thread_id,
    )


@pytest.mark.unit
def test_a_fenced_authority_stages_its_local_card_instead_of_writing(fenced_ct: dict[str, Any]) -> None:
    tid, conv = fenced_ct["thread"], fenced_ct["conv"]
    fenced_ct["runs"].create_run(_run(tid))
    assert handle_run_status_change(_run(tid), "succeeded", summary="done") is None
    assert conv.list_messages(tid) == []  # no write under a stale epoch
    pending = conv.barb_outbox().pending_for(tid, "run-1")
    assert [e.terminal for e in pending] == [True]  # the terminal card waits for reconfiguration


@pytest.mark.unit
def test_status_reports_a_fenced_authority_as_degraded(fenced_ct: dict[str, Any]) -> None:
    status = barb_runtime.barb_status()
    assert status["role"] == "authority" and status["epoch"] == 1
    assert status["fence"] == {
        "epoch": 2,
        "node": "DESK",
        "reason": "failover",
        "updated_at": status["fence"]["updated_at"],
    }
    assert status["fenced"] is True and status["degraded"] is True


@pytest.mark.unit
def test_status_of_a_current_authority_is_not_fenced(
    fenced_ct: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BARB_AUTHORITY_NODE", "CT")
    monkeypatch.setenv("BARB_AUTHORITY_EPOCH", "3")
    barb_runtime.reset_authority_config()
    status = barb_runtime.barb_status()
    assert status["fenced"] is False and status["degraded"] is False
