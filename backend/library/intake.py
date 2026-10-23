"""Content-addressed chat attachment storage and Librarian Intake Queue."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .db import LibraryStore, init_library_db

MAX_INTAKE_BYTES = 10 * 1024 * 1024
VALID_SENSITIVITIES = frozenset({"public_reference", "confidential_work", "vault_sensitive"})
_SECRET_FILENAME_PATTERNS = re.compile(
    r"(^|/|\.)(env|pem|key|p12|pfx|id_rsa|id_ed25519|credentials|netrc)(\.|$)",
    re.IGNORECASE,
)
_SECRET_CONTENT_PATTERNS = (
    re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(rb"(?m)^[A-Z0-9_]*(SECRET|TOKEN|PASSWORD|API_KEY|PRIVATE_KEY)\s*=\s*\S+"),
)


def _sanitize_filename(filename: str) -> str:
    cleaned = Path(filename.strip()).name
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("filename must be a valid non-empty file name")
    return cleaned[:200]


def classify_sensitivity(filename: str, content: bytes, requested: str | None = None) -> str:
    """Classify attachment sensitivity before writing bytes to disk (fail-closed)."""
    req = (requested or "confidential_work").strip().lower()
    base = req if req in VALID_SENSITIVITIES else "confidential_work"
    if base == "vault_sensitive":
        return "vault_sensitive"
    if _SECRET_FILENAME_PATTERNS.search(filename):
        return "vault_sensitive"
    sample = content[:262144]
    if any(pattern.search(sample) for pattern in _SECRET_CONTENT_PATTERNS):
        return "vault_sensitive"
    return base


def _keyed_sensitive_digest(content: bytes) -> str:
    secret = os.environ.get("HUB_FLEET_TOKEN") or "runner-dashboard-intake-hmac-v1"
    mac = hmac.new(secret.encode("utf-8"), content, hashlib.sha256).hexdigest()
    return f"hmac256:{mac}"


def _write_cas_blob(db_path: Path, sha256: str, content: bytes) -> Path:
    root = db_path.parent / "chat_attachments" / sha256[:2]
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root.parent, 0o700)
    os.chmod(root, 0o700)
    dest = root / sha256
    if dest.exists():
        return dest
    fd, tmp_name = tempfile.mkstemp(dir=str(root), prefix=".tmp-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(tmp_name, dest)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    return dest


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "sha256": str(row["sha256"]),
        "filename": str(row["filename"]),
        "size_bytes": int(row["size_bytes"]),
        "mime_type": str(row["mime_type"]),
        "sensitivity": str(row["sensitivity"]),
        "status": str(row["status"]),
        "thread_id": row["thread_id"],
        "uploaded_by": row["uploaded_by"],
        "work_id": int(row["work_id"]) if row["work_id"] is not None else None,
        "created_at": str(row["created_at"]),
        "promoted_at": row["promoted_at"],
        "promoted_by": row["promoted_by"],
    }


def decode_base64_payload(content_base64: str) -> bytes:
    """Decode base64 attachment payload and enforce MAX_INTAKE_BYTES."""
    try:
        raw = base64.b64decode(content_base64.encode("ascii"), validate=True)
    except Exception as exc:
        raise ValueError("content_base64 must be valid base64") from exc
    if len(raw) > MAX_INTAKE_BYTES:
        raise ValueError(f"attachment exceeds maximum size ({MAX_INTAKE_BYTES} bytes)")
    return raw


def stage_intake_item(
    db_path: Path,
    *,
    filename: str,
    content: bytes,
    mime_type: str = "application/octet-stream",
    requested_sensitivity: str | None = None,
    thread_id: str | None = None,
    uploaded_by: str = "operator",
) -> dict[str, Any]:
    """Classify and stage an uploaded chat file into the Librarian Intake Queue."""
    if len(content) > MAX_INTAKE_BYTES:
        raise ValueError(f"attachment exceeds maximum size ({MAX_INTAKE_BYTES} bytes)")
    safe_name = _sanitize_filename(filename)
    sensitivity = classify_sensitivity(safe_name, content, requested_sensitivity)
    if sensitivity == "vault_sensitive":
        digest = _keyed_sensitive_digest(content)
        storage_path = ""
        status = "quarantined_sensitive"
    else:
        digest = hashlib.sha256(content).hexdigest()
        storage_path = str(_write_cas_blob(db_path, digest, content))
        status = "staged"
    init_library_db(db_path)
    now = datetime.now(UTC).isoformat()
    with closing(LibraryStore(db_path).connect()) as conn:
        cur = conn.execute(
            "INSERT INTO library_intake(sha256,filename,size_bytes,mime_type,sensitivity,"
            "status,thread_id,uploaded_by,storage_path,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                digest,
                safe_name,
                len(content),
                mime_type or "application/octet-stream",
                sensitivity,
                status,
                thread_id,
                uploaded_by,
                storage_path,
                now,
            ),
        )
        intake_id = int(cur.lastrowid or 0)
        conn.commit()
        row = conn.execute("SELECT * FROM library_intake WHERE id = ?", (intake_id,)).fetchone()
    assert row is not None
    return _row_to_dict(row)


def list_intake_items(
    db_path: Path,
    *,
    status: str | None = None,
    sensitivity: str | None = None,
) -> list[dict[str, Any]]:
    """List Librarian Intake Queue items newest first without exposing storage paths."""
    init_library_db(db_path)
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if sensitivity:
        clauses.append("sensitivity = ?")
        params.append(sensitivity)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with closing(LibraryStore(db_path).connect()) as conn:
        rows = conn.execute(
            f"SELECT * FROM library_intake {where_sql} ORDER BY id DESC LIMIT 200",
            tuple(params),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _index_promoted_work(conn: sqlite3.Connection, row: sqlite3.Row, text_body: str) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO library_sources(id,label,root_path,shared,created_at,status) "
        "VALUES('chat-intake','Promoted Chat Intake','chat-intake://promoted',1,?, 'active')",
        (datetime.now(UTC).isoformat(),),
    )
    sha256 = str(row["sha256"])
    filename = str(row["filename"])
    ext = Path(filename).suffix.lstrip(".").lower() or "txt"
    existing = conn.execute("SELECT id FROM works WHERE sha256 = ?", (sha256,)).fetchone()
    if existing:
        work_id = int(existing["id"])
    else:
        cur = conn.execute(
            "INSERT INTO works(sha256,title,format,provenance,status) VALUES(?,?,?,'chat_intake','active')",
            (sha256, Path(filename).stem, ext),
        )
        work_id = int(cur.lastrowid or 0)
        if text_body.strip():
            tokens = max(1, len(text_body.split()))
            p_cur = conn.execute(
                "INSERT INTO passages(work_id,page_start,page_end,section,text,token_estimate) VALUES(?,1,1,?,?,?)",
                (work_id, filename, text_body[:20000], tokens),
            )
            conn.execute(
                "INSERT INTO passages_fts(rowid,title,text) VALUES(?,?,?)",
                (int(p_cur.lastrowid or 0), Path(filename).stem, text_body[:20000]),
            )
    conn.execute(
        "INSERT OR REPLACE INTO files(work_id,source_id,rel_path,mtime,size,sha256,status,source_root) "
        "VALUES(?, 'chat-intake', ?, ?, ?, ?, 'present', ?)",
        (work_id, f"{row['id']}/{filename}", time.time(), int(row["size_bytes"]), sha256, str(row["storage_path"])),
    )
    return work_id


def promote_intake_item(db_path: Path, intake_id: int, *, promoted_by: str) -> dict[str, Any]:
    """Explicitly promote an eligible intake item into Alexandria works and passages."""
    init_library_db(db_path)
    with closing(LibraryStore(db_path).connect()) as conn:
        row = conn.execute("SELECT * FROM library_intake WHERE id = ?", (intake_id,)).fetchone()
        if row is None:
            raise KeyError(f"intake item {intake_id} not found")
        if str(row["sensitivity"]) == "vault_sensitive" or str(row["status"]) == "quarantined_sensitive":
            raise PermissionError("vault_sensitive items cannot be promoted to the shared library")
        storage_path = Path(str(row["storage_path"])) if row["storage_path"] else None
        if storage_path is None or not storage_path.is_file():
            raise FileNotFoundError(f"staged blob missing for intake item {intake_id}")
        raw_bytes = storage_path.read_bytes()
        text_body = raw_bytes.decode("utf-8", errors="replace")
        work_id = _index_promoted_work(conn, row, text_body)
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "UPDATE library_intake SET status = 'promoted', work_id = ?, promoted_at = ?, promoted_by = ? WHERE id = ?",
            (work_id, now, promoted_by, intake_id),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM library_intake WHERE id = ?", (intake_id,)).fetchone()
    assert updated is not None
    return _row_to_dict(updated)


def format_attachment_for_prompt(item: dict[str, Any]) -> str:
    """Format an intake attachment reference safely for LLM prompt context."""
    filename = str(item.get("filename", "attachment"))
    size_bytes = int(item.get("size_bytes", 0))
    sensitivity = str(item.get("sensitivity", "confidential_work"))
    if sensitivity == "vault_sensitive":
        return f"[vault_sensitive attachment redacted: {filename} ({size_bytes} bytes)]"
    return f"[attachment #{item.get('id')}: {filename} ({size_bytes} bytes, {sensitivity})]"
