"""Append-only, metadata-only audit for private-context access (issue #3096,
epic #3077 private-roles slice 3).

Records role, template, provider, host, time and outcome for every
``private_roles.read_private_context`` call, never the decrypted content, in
a store independent of role definitions so deleting a role never deletes its
trail. A failed write fails closed (see ``private_roles.read_private_context``).
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from staff.conversation_migrations import _online_backup
from staff.store import _config_dir, _now, first_touch_lock

log = logging.getLogger("dashboard.private_context_audit")

# Mirrors private_roles._REASONS + "success"/"restore" (a literal to dodge an import cycle).
OUTCOMES: frozenset[str] = frozenset(
    {
        "success",
        "role-not-found",
        "not-owner",
        "template-not-allowlisted",
        "provider-not-approved",
        "key-missing",
        "store-missing",
        "version-skew",
        "decrypt-failed",
        "restore",
    }
)

DEFAULT_RETENTION_DAYS = 180


class PrivateContextAuditError(RuntimeError):
    """An audit write failed; the caller must fail closed."""


@dataclass(frozen=True)
class PrivateContextAuditRecord:
    """One metadata-only row. Never carries decrypted content."""

    role_id: str
    template_name: str
    provider_cli: str
    host: str
    outcome: str
    ts: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown private-context audit outcome: {self.outcome}")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS private_context_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    role_id TEXT NOT NULL,
    template_name TEXT NOT NULL,
    provider_cli TEXT NOT NULL,
    host TEXT NOT NULL,
    outcome TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS private_context_audit_ts_idx ON private_context_audit(ts);
CREATE INDEX IF NOT EXISTS private_context_audit_role_idx ON private_context_audit(role_id);
"""


def default_audit_db_path() -> Path:
    return Path(
        os.environ.get("RD_PRIVATE_CONTEXT_AUDIT_DB", str(_config_dir() / "private_context_audit.sqlite3"))
    ).expanduser()


class PrivateContextAuditStore:
    """Thread-safe, append-only SQLite store. Every write fails closed."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_audit_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with first_touch_lock(self.path), self._lock:
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=30.0)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA busy_timeout = 30000")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.executescript(_SCHEMA)

    def record(self, rec: PrivateContextAuditRecord) -> int:
        """Insert one append-only row. Raises on any failure (fail closed)."""
        try:
            with self._lock:
                cursor = self._conn.execute(
                    "INSERT INTO private_context_audit "
                    "(ts, role_id, template_name, provider_cli, host, outcome) VALUES (?, ?, ?, ?, ?, ?)",
                    (rec.ts, rec.role_id, rec.template_name, rec.provider_cli, rec.host, rec.outcome),
                )
                return int(cursor.lastrowid or 0)
        except Exception as exc:
            log.error("Private-context audit write failed (failing closed): %s", exc)
            raise PrivateContextAuditError(f"private-context audit write failed: {exc}") from exc

    def list_entries(self, *, role_id: str | None = None, limit: int = 100) -> list[PrivateContextAuditRecord]:
        clauses: list[str] = []
        params: list[str] = []
        if role_id:
            clauses.append("role_id = ?")
            params.append(role_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM private_context_audit {where} ORDER BY ts DESC, id DESC LIMIT ?"  # noqa: S608
        with self._lock:
            rows = self._conn.execute(query, (*params, int(limit))).fetchall()
        return [_record_from_row(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _record_from_row(row: sqlite3.Row) -> PrivateContextAuditRecord:
    return PrivateContextAuditRecord(
        role_id=row["role_id"],
        template_name=row["template_name"],
        provider_cli=row["provider_cli"],
        host=row["host"],
        outcome=row["outcome"],
        ts=row["ts"],
    )


_store: PrivateContextAuditStore | None = None
_store_lock = threading.Lock()


def get_audit_store() -> PrivateContextAuditStore:
    global _store  # noqa: PLW0603
    with _store_lock:
        if _store is None or _store.path != default_audit_db_path():
            _store = PrivateContextAuditStore()
        return _store


def record_access(
    *,
    role_id: str,
    template_name: str,
    provider_cli: str,
    host: str,
    outcome: str,
    store: PrivateContextAuditStore | None = None,
) -> int:
    """Used by private_roles.read_private_context; always fails closed on error."""
    rec = PrivateContextAuditRecord(
        role_id=role_id, template_name=template_name, provider_cli=provider_cli, host=host, outcome=outcome
    )
    return (store or get_audit_store()).record(rec)


def archive_old_entries(
    store: PrivateContextAuditStore | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    archive_dir: Path | None = None,
    reference_date: str | None = None,
) -> tuple[int, list[Path]]:
    """Archive rows older than retention_days to gzip then delete from the live DB.

    Never deletes before archiving: an archive-write failure raises and leaves every live row untouched.
    """
    target_store = store or get_audit_store()
    out_dir = archive_dir or (_config_dir() / "private_context_audit_archive")
    out_dir.mkdir(parents=True, exist_ok=True)

    ref_dt = datetime.fromisoformat(reference_date.replace("Z", "+00:00")) if reference_date else datetime.now(UTC)
    cutoff_iso = (ref_dt - timedelta(days=retention_days)).isoformat().replace("+00:00", "Z")

    with target_store._lock:  # noqa: SLF001
        rows = target_store._conn.execute(  # noqa: SLF001
            "SELECT * FROM private_context_audit WHERE ts < ? ORDER BY ts ASC", (cutoff_iso,)
        ).fetchall()
        if not rows:
            return 0, []
        records = [_record_from_row(r) for r in rows]
        archive_path = out_dir / f"private_context_audit_{cutoff_iso[:7].replace('-', '')}.jsonl.gz"
        with gzip.open(archive_path, "at", encoding="utf-8") as gz:
            for rec in records:
                gz.write(json.dumps(rec.__dict__, separators=(",", ":")) + "\n")
        ids = [r["id"] for r in rows]
        chunk = 500
        for i in range(0, len(ids), chunk):
            batch = ids[i : i + chunk]
            marks = ", ".join("?" for _ in batch)
            target_store._conn.execute(  # noqa: SLF001
                f"DELETE FROM private_context_audit WHERE id IN ({marks})",  # noqa: S608
                tuple(batch),
            )
    log.info("Archived %d private-context audit entries to %s", len(records), archive_path)
    return len(records), [archive_path]


def backup_encrypted(db_path: Path, dest_path: Path, key: bytes) -> Path | None:
    """Snapshot db_path (WAL-aware), Fernet-encrypt it to dest_path; fail closed, leaving no partial file."""
    if not db_path.exists() or db_path.stat().st_size == 0:
        return None
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_db = dest_path.with_name(dest_path.name + ".tmp-snapshot")
    try:
        _online_backup(db_path, tmp_db)
        ciphertext = Fernet(key).encrypt(tmp_db.read_bytes())
        dest_path.write_bytes(ciphertext)
    except (sqlite3.Error, OSError, ValueError) as exc:
        log.warning("Private-context audit backup failed: %s", exc)
        dest_path.unlink(missing_ok=True)
        return None
    finally:
        tmp_db.unlink(missing_ok=True)
    return dest_path


def restore_encrypted(
    backup_path: Path,
    restore_db_path: Path,
    key: bytes,
    *,
    store: PrivateContextAuditStore | None = None,
) -> bool:
    """Decrypt, validate and restore backup_path to restore_db_path; audit the restore.

    A corrupted backup or wrong key is rejected (returns False) before
    anything is written; success adds one "restore" audit entry.
    """
    try:
        plaintext = Fernet(key).decrypt(backup_path.read_bytes())
    except (OSError, InvalidToken, ValueError) as exc:
        log.warning("Private-context audit restore rejected: %s", exc)
        return False
    restore_db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_db = restore_db_path.with_name(restore_db_path.name + ".tmp-restore")
    try:
        tmp_db.write_bytes(plaintext)
        conn = sqlite3.connect(str(tmp_db))
        try:
            conn.execute("PRAGMA integrity_check").fetchone()
            conn.executescript(_SCHEMA)  # validated before anything downstream trusts it
        finally:
            conn.close()
        tmp_db.replace(restore_db_path)
    except (sqlite3.Error, OSError) as exc:
        log.warning("Private-context audit restore rejected (invalid database): %s", exc)
        tmp_db.unlink(missing_ok=True)
        return False
    restored_store = store or PrivateContextAuditStore(restore_db_path)
    restored_store.record(
        PrivateContextAuditRecord(role_id="", template_name="", provider_cli="", host="", outcome="restore")
    )
    return True
