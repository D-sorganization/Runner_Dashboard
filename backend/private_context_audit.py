"""Append-only, metadata-only audit for private-context access (issue #3096,
epic #3077 private-roles slice 3).

Records role, template, provider, host, time and outcome for every
``private_roles.read_private_context`` call, never the decrypted content, in
a store independent of role definitions so deleting a role never deletes its
trail. A failed write fails closed (see ``private_roles.read_private_context``).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from private_mode import DISABLED, Level, PrivateModeConfig
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
GENESIS_HASH = "0" * 64
_FORBIDDEN_META_KEYS = frozenset(
    {"body", "content", "prompt", "transcript", "secret", "token", "password", "plaintext", "raw"}
)


class PrivateContextAuditError(RuntimeError):
    """An audit write failed; the caller must fail closed."""


@dataclass(frozen=True)
class PrivateModeAuditEvent:
    """One metadata-only Private Mode decision event (issue #3260, PM-9)."""

    action: str
    actor: str
    target_id: str
    level: Level
    outcome: str
    metadata: Mapping[str, str | int | bool] = field(default_factory=dict)
    ts: str = field(default_factory=_now)
    seq: int = 0
    prev_hash: str = ""
    entry_hash: str = ""

    def __post_init__(self) -> None:
        for name, val in (
            ("action", self.action),
            ("actor", self.actor),
            ("target_id", self.target_id),
            ("outcome", self.outcome),
        ):
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"private_mode_audit: {name} must be a non-empty str")
        if not isinstance(self.level, Level):
            raise ValueError("private_mode_audit: level must be a Level")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("private_mode_audit: metadata must be a mapping")
        for k, v in self.metadata.items():
            if not isinstance(k, str) or k.lower() in _FORBIDDEN_META_KEYS:
                raise ValueError("private_mode_audit: forbidden content key in metadata")
            if not isinstance(v, (str, int, bool)) or (isinstance(v, str) and len(v) > 256):
                raise ValueError("private_mode_audit: metadata values must be short scalars")


@dataclass(frozen=True)
class AuditDecisionResult:
    """Outcome of recording a Private Mode audit decision."""

    row_id: int
    parked: bool
    state: str
    reason: str = ""
    entry_hash: str = ""


@dataclass(frozen=True)
class ChainVerificationResult:
    """Result of verifying the hash chain and independent checkpoint."""

    ok: bool
    checked_count: int
    reason: str = ""


def resolve_retention_days(env: Mapping[str, str] | None = None) -> int:
    """Resolve and validate audit retention days from environment mapping."""
    source = os.environ if env is None else env
    raw = source.get("RD_PRIVATE_AUDIT_RETENTION_DAYS")
    if raw is None or not raw.strip():
        return DEFAULT_RETENTION_DAYS
    try:
        days = int(raw.strip())
    except ValueError as exc:
        raise ValueError("private_mode_audit: retention days must be an int") from exc
    if days < 1 or days > 3650:
        raise ValueError("private_mode_audit: retention days must be between 1 and 3650")
    return days


def _compute_entry_hash(parts: Sequence[object]) -> str:
    payload = json.dumps(list(parts), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
CREATE TABLE IF NOT EXISTS private_mode_audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, action TEXT NOT NULL,
    actor TEXT NOT NULL, target_id TEXT NOT NULL, level TEXT NOT NULL, outcome TEXT NOT NULL,
    metadata_json TEXT NOT NULL, prev_hash TEXT NOT NULL, entry_hash TEXT NOT NULL
);
"""


def default_audit_db_path() -> Path:
    return Path(
        os.environ.get("RD_PRIVATE_CONTEXT_AUDIT_DB", str(_config_dir() / "private_context_audit.sqlite3"))
    ).expanduser()


class PrivateContextAuditStore:
    """Thread-safe, append-only SQLite store. Every write fails closed."""

    def __init__(self, path: Path | None = None, *, checkpoint_path: Path | None = None) -> None:
        self.path = path or default_audit_db_path()
        self.checkpoint_path = checkpoint_path or self.path.with_suffix(".checkpoint.json")
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

    def record_decisions_batch(
        self, events: Sequence[PrivateModeAuditEvent], *, config: PrivateModeConfig = DISABLED
    ) -> list[AuditDecisionResult]:
        """Group-commit a batch of Private Mode decisions; fail closed for SENSITIVE only."""
        if not isinstance(config, PrivateModeConfig):
            raise ValueError("private_mode_audit: config must be a PrivateModeConfig")
        if not all(isinstance(ev, PrivateModeAuditEvent) for ev in events):
            raise ValueError("private_mode_audit: all items must be PrivateModeAuditEvent")
        if not config.enabled:
            return [AuditDecisionResult(row_id=0, parked=False, state="DISABLED") for _ in events]
        if not events:
            return []
        try:
            with self._lock:
                last_row = self._conn.execute(
                    "SELECT seq, entry_hash FROM private_mode_audit ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                prev_seq = int(last_row["seq"]) if last_row else 0
                prev_hash = str(last_row["entry_hash"]) if last_row else GENESIS_HASH
                results: list[AuditDecisionResult] = []
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    for idx, ev in enumerate(events, start=1):
                        seq = prev_seq + idx
                        meta_json = json.dumps(dict(ev.metadata), sort_keys=True, separators=(",", ":"))
                        parts = (
                            seq,
                            ev.ts,
                            ev.action,
                            ev.actor,
                            ev.target_id,
                            ev.level.name,
                            ev.outcome,
                            meta_json,
                            prev_hash,
                        )
                        ehash = _compute_entry_hash(parts)
                        self._conn.execute(
                            "INSERT INTO private_mode_audit VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (*parts, ehash),
                        )
                        prev_hash = ehash
                        results.append(AuditDecisionResult(row_id=seq, parked=False, state="OK", entry_hash=ehash))
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
                cp_data = {"last_seq": prev_seq + len(events), "last_hash": prev_hash, "ts": events[-1].ts}
                self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                self.checkpoint_path.write_text(json.dumps(cp_data, separators=(",", ":")), encoding="utf-8")
                return results
        except Exception as exc:
            log.error("Private Mode audit batch write failed: %s", exc)
            return [
                AuditDecisionResult(row_id=0, parked=True, state="PARKED_AUDIT", reason="audit-unavailable")
                if ev.level >= Level.SENSITIVE
                else AuditDecisionResult(row_id=0, parked=False, state="OK", reason="audit-degraded-non-sensitive")
                for ev in events
            ]

    def record_decision(
        self, event: PrivateModeAuditEvent, *, config: PrivateModeConfig = DISABLED
    ) -> AuditDecisionResult:
        """Record a single Private Mode decision via the shared writer."""
        return self.record_decisions_batch([event], config=config)[0]

    def list_decisions(self, *, limit: int = 100) -> list[PrivateModeAuditEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM private_mode_audit ORDER BY seq ASC LIMIT ?", (int(limit),)
            ).fetchall()
        return [
            PrivateModeAuditEvent(
                action=r["action"],
                actor=r["actor"],
                target_id=r["target_id"],
                level=Level[r["level"]],
                outcome=r["outcome"],
                metadata=json.loads(r["metadata_json"]),
                ts=r["ts"],
                seq=int(r["seq"]),
                prev_hash=r["prev_hash"],
                entry_hash=r["entry_hash"],
            )
            for r in rows
        ]

    def verify_chain(self) -> ChainVerificationResult:
        """Verify hash-chain continuity and compare against the independent checkpoint."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM private_mode_audit ORDER BY seq ASC").fetchall()
        prev_hash = GENESIS_HASH
        last_seq = 0
        for idx, r in enumerate(rows, start=1):
            seq = int(r["seq"])
            if seq != idx or r["prev_hash"] != prev_hash:
                return ChainVerificationResult(ok=False, checked_count=idx - 1, reason="hash chain discontinuity")
            parts = (
                seq,
                r["ts"],
                r["action"],
                r["actor"],
                r["target_id"],
                r["level"],
                r["outcome"],
                r["metadata_json"],
                prev_hash,
            )
            expected = _compute_entry_hash(parts)
            if r["entry_hash"] != expected:
                return ChainVerificationResult(ok=False, checked_count=idx - 1, reason="entry hash tamper detected")
            prev_hash = expected
            last_seq = seq
        if self.checkpoint_path.exists():
            cp = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            if int(cp.get("last_seq", 0)) > last_seq or (
                int(cp.get("last_seq", 0)) == last_seq and cp.get("last_hash") != prev_hash
            ):
                return ChainVerificationResult(
                    ok=False, checked_count=len(rows), reason="tail truncation or checkpoint mismatch"
                )
        return ChainVerificationResult(ok=True, checked_count=len(rows))

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
