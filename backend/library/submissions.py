from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from library.db import LibraryStore, init_library_db

_MAX_EXCERPT = 4000
_MAX_FIELD = 512
_STATUSES = frozenset({"pending", "accepted", "rejected", "duplicate"})
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.I)


def create_submission(db_path: Path, *, principal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = _normalize_payload(payload, principal_id=principal_id)
    init_library_db(db_path)
    with closing(LibraryStore(db_path).connect()) as conn:
        status = _dedupe_status(conn, body) or "pending"
        cur = conn.execute(
            "INSERT INTO submissions (created_at, status, payload_json) VALUES (?,?,?)",
            (datetime.now(UTC).isoformat(), status, json.dumps(body)),
        )
        conn.commit()
        sub_id = int(cur.lastrowid or 0)
    return {"id": sub_id, "status": status, "payload": body}


def list_submissions(db_path: Path, *, status: str | None = None) -> list[dict[str, Any]]:
    init_library_db(db_path)
    with closing(LibraryStore(db_path).connect()) as conn:
        if status:
            if status not in _STATUSES:
                raise ValueError("invalid status filter")
            rows = conn.execute(
                "SELECT id, created_at, status, payload_json FROM submissions WHERE status=? ORDER BY id DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, created_at, status, payload_json FROM submissions ORDER BY id DESC"
            ).fetchall()
    return [_row(r) for r in rows]


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "created_at": row[1],
        "status": row[2],
        "payload": json.loads(row[3]),
    }


def _normalize_payload(payload: dict[str, Any], *, principal_id: str) -> dict[str, Any]:
    doi = _opt_str(payload.get("doi"))
    if doi and not _DOI_RE.match(doi):
        raise ValueError("invalid doi")
    title = _req_str(payload.get("title"), "title")
    reason = _req_str(payload.get("reason"), "reason")
    requesting_ref = _req_str(payload.get("requesting_ref"), "requesting_ref")
    excerpt = _opt_str(payload.get("excerpt"))
    if excerpt and len(excerpt) > _MAX_EXCERPT:
        raise ValueError("excerpt too long")
    authors = payload.get("authors") or []
    if not isinstance(authors, list) or not authors:
        raise ValueError("authors required")
    authors = [_req_str(a, "author") for a in authors[:20]]
    return {
        "doi": doi,
        "url": _opt_str(payload.get("url")),
        "isbn": _opt_str(payload.get("isbn")),
        "title": title,
        "authors": authors,
        "reason": reason,
        "requesting_ref": requesting_ref,
        "excerpt": excerpt,
        "submitted_by": principal_id,
    }


def _dedupe_status(conn: sqlite3.Connection, body: dict[str, Any]) -> str | None:
    doi, isbn = body.get("doi"), body.get("isbn")
    if doi and conn.execute("SELECT 1 FROM works WHERE doi=? AND status='active'", (doi,)).fetchone():
        return "duplicate"
    if isbn and conn.execute("SELECT 1 FROM works WHERE isbn=? AND status='active'", (isbn,)).fetchone():
        return "duplicate"
    key = json.dumps({"doi": doi, "isbn": isbn, "title": body["title"].lower()}, sort_keys=True)
    for (raw,) in conn.execute("SELECT payload_json FROM submissions WHERE status='pending'"):
        prior = json.loads(raw)
        prior_key = json.dumps(
            {"doi": prior.get("doi"), "isbn": prior.get("isbn"), "title": (prior.get("title") or "").lower()},
            sort_keys=True,
        )
        if prior_key == key:
            return "duplicate"
    return None


def _req_str(value: object, field: str) -> str:
    text = _opt_str(value)
    if not text:
        raise ValueError(f"{field} required")
    return text


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > _MAX_FIELD:
        raise ValueError("field too long")
    return text
