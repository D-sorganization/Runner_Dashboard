"""Encrypted-at-rest Secure Storage Vault with strict 0600 master key and hash-chained audit log."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from platform_utils.vault_key_permissions import assert_master_key_file_permissions

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_HANDLE_PREFIX = "$vault:"
_MASK = "••••••••"


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_vault_key_path() -> Path:
    override = (
        os.environ.get("RUNNER_DASHBOARD_VAULT_KEY_PATH") or os.environ.get("DASHBOARD_VAULT_KEY_PATH") or ""
    ).strip()
    return Path(override).expanduser() if override else Path.home() / ".config" / "runner-dashboard" / "vault.key"


def default_vault_db_path() -> Path:
    override = (
        os.environ.get("RUNNER_DASHBOARD_VAULT_DB_PATH") or os.environ.get("DASHBOARD_VAULT_DB_PATH") or ""
    ).strip()
    return (
        Path(override).expanduser()
        if override
        else Path(os.environ.get("DASHBOARD_STATE_DIR", "state")) / "vault.sqlite3"
    )


def load_or_create_master_key(key_path: Path | None = None) -> bytes:
    """Load 32-byte AES-256 master key or atomically create with mode 0600; fail closed on lax perms."""
    path = key_path or default_vault_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, secrets.token_bytes(32))
        finally:
            os.close(fd)
    assert_master_key_file_permissions(path)
    data = path.read_bytes()
    if len(data) != 32:
        raise ValueError(f"Vault master key {path} must be 32 bytes (got {len(data)})")
    return data


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or default_vault_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS vault_secrets ("
        "key TEXT PRIMARY KEY, scope TEXT NOT NULL DEFAULT 'global', description TEXT NOT NULL DEFAULT '', "
        "nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, fingerprint TEXT NOT NULL, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by TEXT NOT NULL DEFAULT 'operator');"
        "CREATE TABLE IF NOT EXISTS vault_audit_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, secret_key TEXT NOT NULL, "
        "actor TEXT NOT NULL, scope TEXT NOT NULL, outcome TEXT NOT NULL, "
        "prev_hash TEXT NOT NULL, entry_hash TEXT NOT NULL, created_at TEXT NOT NULL);"
    )
    return conn


def _append_audit(
    conn: sqlite3.Connection, *, action: str, secret_key: str, actor: str, scope: str, outcome: str
) -> None:
    prev_row = conn.execute("SELECT entry_hash FROM vault_audit_log ORDER BY id DESC LIMIT 1").fetchone()
    prev_hash = str(prev_row["entry_hash"]) if prev_row else "0" * 64
    now = _utc_now()
    entry_hash = hashlib.sha256(
        f"{prev_hash}|{action}|{secret_key}|{actor}|{scope}|{outcome}|{now}".encode()
    ).hexdigest()
    conn.execute(
        "INSERT INTO vault_audit_log(action, secret_key, actor, scope, outcome, prev_hash, entry_hash, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (action, secret_key, actor, scope, outcome, prev_hash, entry_hash, now),
    )


def _aad(key: str, scope: str) -> bytes:
    return f"{key}:{scope}".encode()


def put_vault_secret(
    *,
    key: str,
    value: str,
    scope: str = "global",
    description: str = "",
    actor: str = "operator",
    db_path: Path | None = None,
    key_path: Path | None = None,
) -> dict[str, Any]:
    norm_key = key.strip().upper()
    if not _KEY_RE.match(norm_key):
        raise ValueError("Vault key must match ^[A-Z][A-Z0-9_]{1,63}$")
    if not value:
        raise ValueError("Vault secret value must be non-empty")
    norm_scope = scope.strip() or "global"
    master_key = load_or_create_master_key(key_path)
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(master_key).encrypt(nonce, value.encode(), _aad(norm_key, norm_scope))
    fingerprint = "sha256:" + hashlib.sha256(ciphertext).hexdigest()[:16]
    now = _utc_now()
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO vault_secrets(key,scope,description,nonce,ciphertext,"
            "fingerprint,created_at,updated_at,created_by) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET scope=excluded.scope, "
            "description=excluded.description, nonce=excluded.nonce, ciphertext=excluded.ciphertext, "
            "fingerprint=excluded.fingerprint, updated_at=excluded.updated_at, created_by=excluded.created_by",
            (norm_key, norm_scope, description.strip(), nonce, ciphertext, fingerprint, now, now, actor),
        )
        _append_audit(conn, action="write", secret_key=norm_key, actor=actor, scope=norm_scope, outcome="ok")
        conn.commit()
    return {
        "key": norm_key,
        "scope": norm_scope,
        "description": description.strip(),
        "handle": f"{_HANDLE_PREFIX}{norm_key}",
        "masked_value": _MASK,
        "fingerprint": fingerprint,
        "updated_at": now,
        "created_by": actor,
    }


def list_vault_secrets(
    *, actor: str = "operator", db_path: Path | None = None, key_path: Path | None = None
) -> list[dict[str, Any]]:
    load_or_create_master_key(key_path)
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT key, scope, description, fingerprint, created_at, updated_at, created_by "
            "FROM vault_secrets ORDER BY key ASC"
        ).fetchall()
        _append_audit(conn, action="list", secret_key="*", actor=actor, scope="*", outcome="ok")
        conn.commit()
    return [
        {
            "key": r["key"],
            "scope": r["scope"],
            "description": r["description"],
            "handle": f"{_HANDLE_PREFIX}{r['key']}",
            "masked_value": _MASK,
            "fingerprint": r["fingerprint"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "created_by": r["created_by"],
        }
        for r in rows
    ]


def delete_vault_secret(*, key: str, actor: str = "operator", db_path: Path | None = None) -> bool:
    norm_key = key.strip().upper()
    with closing(_connect(db_path)) as conn:
        deleted = conn.execute("DELETE FROM vault_secrets WHERE key = ?", (norm_key,)).rowcount > 0
        _append_audit(
            conn,
            action="delete",
            secret_key=norm_key,
            actor=actor,
            scope="global",
            outcome="ok" if deleted else "not_found",
        )
        conn.commit()
    return deleted


def resolve_vault_handle(
    handle: str,
    *,
    actor: str,
    scope: str = "global",
    db_path: Path | None = None,
    key_path: Path | None = None,
) -> str:
    """Internal-only resolution of `$vault:<KEY>` with scope check and audit trail."""
    if not handle.startswith(_HANDLE_PREFIX):
        raise ValueError(f"Invalid vault handle: {handle!r}")
    norm_key = handle[len(_HANDLE_PREFIX) :].strip().upper()
    master_key = load_or_create_master_key(key_path)
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT key, scope, nonce, ciphertext FROM vault_secrets WHERE key = ?", (norm_key,)
        ).fetchone()
        if row is None:
            _append_audit(conn, action="resolve", secret_key=norm_key, actor=actor, scope=scope, outcome="not_found")
            conn.commit()
            raise KeyError(f"Vault secret {norm_key!r} not found")
        row_scope = str(row["scope"])
        if row_scope not in {"global", scope}:
            _append_audit(conn, action="resolve", secret_key=norm_key, actor=actor, scope=scope, outcome="denied_scope")
            conn.commit()
            raise PermissionError(f"Vault secret {norm_key!r} scope {row_scope!r} denied for {scope!r}")
        plaintext = AESGCM(master_key).decrypt(bytes(row["nonce"]), bytes(row["ciphertext"]), _aad(norm_key, row_scope))
        _append_audit(conn, action="resolve", secret_key=norm_key, actor=actor, scope=scope, outcome="ok")
        conn.commit()
        return plaintext.decode()


def list_vault_audit_log(*, limit: int = 100, db_path: Path | None = None) -> list[dict[str, Any]]:
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT id, action, secret_key, actor, scope, outcome, prev_hash, entry_hash, created_at "
            "FROM vault_audit_log ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 500)),),
        ).fetchall()
    return [dict(r) for r in rows]
