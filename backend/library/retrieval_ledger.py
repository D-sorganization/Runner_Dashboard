"""Append-only bounded retrieval identities for per-run library citation audits."""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
_NODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,63}$")
_SOURCE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_DIGEST = re.compile(r"^[a-fA-F0-9]{64}$")
_FIELDS = frozenset({"node", "source_id", "work_id", "passage_id", "file_sha256", "page_start", "page_end"})
_MAX_REFERENCES_PER_CALL = 100
_MAX_REFERENCES_PER_RUN = 500

_SCHEMA = """CREATE TABLE IF NOT EXISTS staff_run_references (
    run_id TEXT NOT NULL,
    citation TEXT NOT NULL,
    valid INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT 'retrieved',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    verified_at TEXT,
    node TEXT NOT NULL,
    source_id TEXT NOT NULL,
    work_id INTEGER NOT NULL CHECK(work_id > 0),
    passage_id INTEGER NOT NULL CHECK(passage_id > 0),
    file_sha256 TEXT NOT NULL CHECK(length(file_sha256) = 64),
    page_start INTEGER NOT NULL CHECK(page_start > 0),
    page_end INTEGER NOT NULL CHECK(page_end >= page_start),
    retrieved_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY(run_id,node,source_id,work_id,passage_id,file_sha256,page_start,page_end)
)"""


def _canonical_reference(reference: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(reference, dict) or set(reference) != _FIELDS:
        raise ValueError("reference must contain only the canonical retrieval identity fields")
    node = reference["node"]
    source_id = reference["source_id"]
    if not isinstance(node, str) or not _NODE.fullmatch(node):
        raise ValueError("invalid reference node")
    if not isinstance(source_id, str) or not _SOURCE.fullmatch(source_id):
        raise ValueError("invalid reference source")
    values: dict[str, Any] = {}
    for key in ("work_id", "passage_id", "page_start", "page_end"):
        value = reference[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be an integer")
        values[key] = value
    if values["work_id"] < 1 or values["passage_id"] < 1 or values["page_start"] < 1:
        raise ValueError("work, passage, and page identifiers must be positive")
    if values["page_end"] < values["page_start"]:
        raise ValueError("page_end must be greater than or equal to page_start")
    digest = reference["file_sha256"]
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ValueError("file_sha256 must be a full SHA-256 digest")
    digest = digest.lower()
    cite = (
        f"[ref:{node}:{source_id}:w{values['work_id']}:p{values['passage_id']}"
        f"#sha256={digest[:8]}:p.{values['page_start']}-{values['page_end']}]"
    )
    return {
        "node": node,
        "source_id": source_id,
        **values,
        "file_sha256": digest,
        "citation": cite,
    }


def record_retrievals(db: Path, *, run_id: str, references: list[dict[str, Any]]) -> int:
    """Record at most 100 canonical identities, deduplicated per run.

    Run existence and caller ownership are checked at the API boundary. No passage
    text or local path is accepted or persisted here.
    """
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run_id")
    if not isinstance(references, list) or len(references) > _MAX_REFERENCES_PER_CALL:
        raise ValueError("at most 100 retrieval references may be recorded")
    canonical = [_canonical_reference(reference) for reference in references]
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(_SCHEMA)
        existing = conn.execute("SELECT count(*) FROM staff_run_references WHERE run_id=?", (run_id,)).fetchone()[0]
        known = {
            (
                row["node"],
                row["source_id"],
                row["work_id"],
                row["passage_id"],
                row["file_sha256"],
                row["page_start"],
                row["page_end"],
            )
            for row in conn.execute(
                "SELECT node,source_id,work_id,passage_id,file_sha256,page_start,page_end "
                "FROM staff_run_references WHERE run_id=?",
                (run_id,),
            )
        }
        new_identities = {
            (
                row["node"],
                row["source_id"],
                row["work_id"],
                row["passage_id"],
                row["file_sha256"],
                row["page_start"],
                row["page_end"],
            )
            for row in canonical
        } - known
        if existing + len(new_identities) > _MAX_REFERENCES_PER_RUN:
            raise ValueError("run retrieval ledger exceeds 500 references")
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO staff_run_references "
            "(run_id,node,source_id,work_id,passage_id,file_sha256,page_start,page_end,citation) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            [
                (
                    run_id,
                    row["node"],
                    row["source_id"],
                    row["work_id"],
                    row["passage_id"],
                    row["file_sha256"],
                    row["page_start"],
                    row["page_end"],
                    row["citation"],
                )
                for row in canonical
            ],
        )
        conn.commit()
        return conn.total_changes - before


def references_for_run(db: Path, run_id: str) -> list[dict[str, Any]]:
    """Return canonical identities in stable order for citation verification."""
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run_id")
    db_path = Path(db)
    try:
        db_path.stat()
    except FileNotFoundError:
        return []
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT run_id,node,source_id,work_id,passage_id,file_sha256,page_start,page_end,citation,"
                "valid,reason,evidence_json,verified_at,retrieved_at "
                "FROM staff_run_references WHERE run_id=? "
                "ORDER BY node,source_id,work_id,passage_id,file_sha256,page_start,page_end",
                (run_id,),
            ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return []
        raise
    return [
        {
            "run_id": run_id_value,
            "node": node,
            "source_id": source_id,
            "work_id": work_id,
            "passage_id": passage_id,
            "file_sha256": digest,
            "page_start": page_start,
            "page_end": page_end,
            "citation": citation,
            "valid": bool(valid),
            "reason": reason,
            "evidence": evidence_json,
            "verified_at": verified_at,
            "retrieved_at": retrieved_at,
        }
        for (
            run_id_value,
            node,
            source_id,
            work_id,
            passage_id,
            digest,
            page_start,
            page_end,
            citation,
            valid,
            reason,
            evidence_json,
            verified_at,
            retrieved_at,
        ) in rows
    ]


def authorize_run_owner(run_store: Any, *, run_id: str, principal_id: str) -> None:
    """Require the authenticated requester or the exact active run identity."""
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run_id")
    if not isinstance(principal_id, str) or not principal_id:
        raise PermissionError("run owner identity is unavailable")
    run = run_store.get_run(run_id)
    if run is None:
        raise LookupError("run not found")
    requested_by = getattr(run, "requested_by", "")
    if requested_by in (principal_id, f"principal:{principal_id}"):
        return
    from staff.tokens import is_active_run_principal

    if not is_active_run_principal(run_id=run_id, role=getattr(run, "role", ""), principal_id=principal_id):
        raise PermissionError("run belongs to another principal")
