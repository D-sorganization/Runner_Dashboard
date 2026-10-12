"""Restoring the Barb authority from a backup preserves IDs and receipts (#1800, BR-06 acceptance).

The authority runs SQLite in WAL mode, so committed rows can live only in the ``-wal`` sidecar until a
checkpoint. A backup that copies just the main database file silently loses them. These tests pin that a
backup taken while the authority is live (uncheckpointed WAL) restores the same thread IDs, run-card
versions and receipt IDs, and that replays after the restore stay deduplicated (no regression).
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from staff.barb_versions import ApplyOutcome, ApplyResult
from staff.conversation_migrations import backup_database
from staff.conversations import ConversationStore
from staff.run_link import run_card_id


def _apply(store: ConversationStore, thread_id: str, version: int, status: str) -> ApplyResult:
    _, result = store.apply_run_card_update(
        thread_id=thread_id,
        run_id="run-1",
        message_id=run_card_id("run-1"),
        status=status,
        version=version,
        body_md=f"card {status} v{version}",
        meta={"run_id": "run-1", "status": status},
        author="librarian",
    )
    return result


@pytest.fixture
def live_authority(tmp_path: Path) -> tuple[ConversationStore, Path]:
    """An authority store with automatic checkpoints disabled so committed rows stay in the WAL."""
    db_path = tmp_path / "authority.sqlite3"
    store = ConversationStore(db_path)
    store._conn.execute("PRAGMA wal_autocheckpoint=0")
    return store, db_path


def _restore(backup: Path, tmp_path: Path) -> ConversationStore:
    """Restore exactly what an operator would: the single backup file, no sidecars."""
    target_dir = tmp_path / "restored"
    target_dir.mkdir()
    target = target_dir / "authority.sqlite3"
    shutil.copyfile(backup, target)
    return ConversationStore(target)


@pytest.mark.unit
def test_backup_of_live_wal_authority_preserves_ids_and_receipts(
    live_authority: tuple[ConversationStore, Path], tmp_path: Path
) -> None:
    store, db_path = live_authority
    thread_id = store.create_thread(title="t", kind="direct", role="librarian", created_by="user:d").id
    applied = _apply(store, thread_id, 1, "running")
    assert applied.outcome is ApplyOutcome.APPLIED and applied.receipt_id
    assert Path(f"{db_path}-wal").stat().st_size > 0  # precondition: the rows are only in the WAL

    backup = backup_database(db_path)
    assert backup is not None and backup.exists()
    store.close()

    restored = _restore(backup, tmp_path)
    try:
        assert restored.get_thread(thread_id) is not None
        row = restored._conn.execute("SELECT receipt_id FROM barb_receipts WHERE ref_id = 'run-1:1'").fetchone()
        assert row is not None and row["receipt_id"] == applied.receipt_id
        version = restored._conn.execute("SELECT version FROM run_card_versions WHERE run_id = 'run-1'").fetchone()
        assert version is not None and int(version["version"]) == 1
    finally:
        restored.close()


@pytest.mark.unit
def test_replays_after_restore_are_deduplicated_and_cannot_regress(
    live_authority: tuple[ConversationStore, Path], tmp_path: Path
) -> None:
    store, db_path = live_authority
    thread_id = store.create_thread(title="t", kind="direct", role="librarian", created_by="user:d").id
    _apply(store, thread_id, 1, "running")
    terminal = _apply(store, thread_id, 2, "succeeded")
    backup = backup_database(db_path)
    assert backup is not None
    store.close()

    restored = _restore(backup, tmp_path)
    try:
        # An outbox redelivering after the restore gets the original receipt, not a second apply.
        replay = _apply(restored, thread_id, 2, "succeeded")
        assert replay.outcome is ApplyOutcome.DUPLICATE and replay.receipt_id == terminal.receipt_id
        # A late, lower-version delivery is refused and the terminal state stands.
        late = _apply(restored, thread_id, 1, "running")
        assert late.outcome is ApplyOutcome.STALE and late.version == 2
        receipts = restored._conn.execute("SELECT COUNT(*) AS n FROM barb_receipts").fetchone()["n"]
        assert receipts == 2
    finally:
        restored.close()


@pytest.mark.unit
def test_backup_is_a_self_contained_database(live_authority: tuple[ConversationStore, Path]) -> None:
    store, db_path = live_authority
    store.create_thread(title="t", kind="direct", role="librarian", created_by="user:d")
    backup = backup_database(db_path)
    store.close()
    assert backup is not None
    assert not Path(f"{backup}-wal").exists()
    conn = sqlite3.connect(backup)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.unit
def test_backup_skips_missing_or_empty_database(tmp_path: Path) -> None:
    assert backup_database(tmp_path / "absent.sqlite3") is None
    empty = tmp_path / "empty.sqlite3"
    empty.touch()
    assert backup_database(empty) is None


@pytest.mark.unit
def test_backup_failure_returns_none_and_leaves_no_partial_file(
    live_authority: tuple[ConversationStore, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, db_path = live_authority
    store.create_thread(title="t", kind="direct", role="librarian", created_by="user:d")
    existing = set(db_path.parent.glob("*.bak.*"))
    real_connect = sqlite3.connect

    def _failing_connect(target, *args, **kwargs):  # type: ignore[no-untyped-def]
        if ".bak." in str(target):
            raise sqlite3.OperationalError("disk I/O error")
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr("staff.conversation_migrations.sqlite3.connect", _failing_connect)
    assert backup_database(db_path) is None
    store.close()
    assert set(db_path.parent.glob("*.bak.*")) == existing
