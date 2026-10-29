"""Retention, rotation and encrypted backup/restore tests for issue #3096."""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import private_context_audit as pca  # noqa: E402

ROLE = "mock-private-role"
TEMPLATE = "mock-template"
CLI = "mock-subscription-cli"
HOST = "mock-node"


def _store(tmp_path: Path) -> pca.PrivateContextAuditStore:
    return pca.PrivateContextAuditStore(tmp_path / "audit.sqlite3")


def _record(store: pca.PrivateContextAuditStore, *, ts: str, outcome: str = "success") -> None:
    store.record(
        pca.PrivateContextAuditRecord(
            role_id=ROLE, template_name=TEMPLATE, provider_cli=CLI, host=HOST, outcome=outcome, ts=ts
        )
    )


def test_unknown_outcome_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown"):
        pca.PrivateContextAuditRecord(
            role_id=ROLE, template_name=TEMPLATE, provider_cli=CLI, host=HOST, outcome="not-a-real-outcome"
        )


def test_archive_moves_only_rows_older_than_retention(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _record(store, ts="2026-01-01T00:00:00Z")
    _record(store, ts="2026-10-01T00:00:00Z")
    archive_dir = tmp_path / "archive"

    moved, paths = pca.archive_old_entries(
        store, retention_days=180, archive_dir=archive_dir, reference_date="2026-10-28T00:00:00Z"
    )

    assert moved == 1
    assert len(paths) == 1
    remaining = store.list_entries(role_id=ROLE)
    assert len(remaining) == 1
    assert remaining[0].ts == "2026-10-01T00:00:00Z"
    with gzip.open(paths[0], "rt", encoding="utf-8") as gz:
        lines = [json.loads(line) for line in gz]
    assert len(lines) == 1
    assert lines[0]["ts"] == "2026-01-01T00:00:00Z"


def test_archive_write_failure_leaves_live_rows_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    _record(store, ts="2026-01-01T00:00:00Z")

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(pca.gzip, "open", _boom)

    with pytest.raises(OSError, match="disk full"):
        pca.archive_old_entries(
            store, retention_days=180, archive_dir=tmp_path / "archive", reference_date="2026-10-28T00:00:00Z"
        )

    assert len(store.list_entries(role_id=ROLE)) == 1


def test_backup_restore_round_trip_preserves_entries_and_audits_the_restore(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _record(store, ts="2026-10-01T00:00:00Z")
    _record(store, ts="2026-10-02T00:00:00Z")
    key = Fernet.generate_key()
    backup_path = tmp_path / "off-repo" / "audit.bak.enc"

    result = pca.backup_encrypted(store.path, backup_path, key)
    assert result == backup_path
    assert backup_path.read_bytes() != store.path.read_bytes()  # actually encrypted, not a plain copy

    restore_path = tmp_path / "restored" / "audit.sqlite3"
    assert pca.restore_encrypted(backup_path, restore_path, key) is True

    restored_store = pca.PrivateContextAuditStore(restore_path)
    entries = restored_store.list_entries(limit=100)
    assert {e.ts for e in entries if e.outcome == "success"} == {"2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"}
    assert any(e.outcome == "restore" for e in entries)  # the restore audits itself


@pytest.mark.parametrize("corrupt_bytes", [True, False], ids=["corrupted-backup", "wrong-key"])
def test_corrupted_or_wrong_key_backup_is_rejected_and_nothing_is_written(tmp_path: Path, corrupt_bytes: bool) -> None:
    store = _store(tmp_path)
    _record(store, ts="2026-10-01T00:00:00Z")
    key = Fernet.generate_key()
    backup_path = tmp_path / "audit.bak.enc"
    pca.backup_encrypted(store.path, backup_path, key)
    if corrupt_bytes:
        backup_path.write_bytes(backup_path.read_bytes() + b"corruption")
    restore_key = key if corrupt_bytes else Fernet.generate_key()

    restore_path = tmp_path / "restored.sqlite3"
    ok = pca.restore_encrypted(backup_path, restore_path, restore_key)

    assert ok is False
    assert not restore_path.exists()


def test_backup_of_empty_or_missing_db_is_a_noop(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.sqlite3"
    result = pca.backup_encrypted(missing, tmp_path / "out.enc", Fernet.generate_key())
    assert result is None
