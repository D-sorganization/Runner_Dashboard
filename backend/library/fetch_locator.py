"""Resolve an explicit source/work/page locator against indexed library records."""

from __future__ import annotations

import re
from contextlib import closing
from pathlib import Path

from library.db import LibraryStore
from library.search import AmbiguousPassageError, SearchHit, fetch_passage


def fetch_locator(
    db_path: Path,
    *,
    source_id: str,
    work_id: int,
    page: int,
    shared_only: bool = False,
) -> SearchHit:
    """Resolve a unique active passage covering ``page`` for one source/work."""
    if not isinstance(source_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", source_id):
        raise ValueError("source_id must be a canonical library source identifier")
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in (work_id, page)):
        raise ValueError("work_id and page must be positive integers")
    if not isinstance(shared_only, bool):
        raise ValueError("shared_only must be boolean")
    query = (
        "SELECT DISTINCT p.id,p.page_start,p.page_end,f.sha256 FROM passages p "
        "JOIN works w ON w.id=p.work_id JOIN files f ON f.work_id=w.id "
        "JOIN library_sources s ON s.id=f.source_id "
        "WHERE f.source_id=? AND p.work_id=? AND p.page_start<=? AND p.page_end>=? "
        "AND f.status='present' AND w.status='active' AND s.status='active' "
    )
    if shared_only:
        query += "AND s.shared=1 "
    with closing(LibraryStore(db_path).connect()) as conn:
        rows = conn.execute(query + "ORDER BY p.id,f.sha256 LIMIT 2", (source_id, work_id, page, page)).fetchall()
    if not rows:
        raise LookupError("page locator not found")
    if len(rows) > 1:
        raise AmbiguousPassageError("page locator is ambiguous; use a canonical passage citation")
    passage_id, start, end, digest = rows[0]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise LookupError("page file digest is unavailable")
    return fetch_passage(
        db_path,
        source_id=source_id,
        work_id=work_id,
        passage_id=passage_id,
        sha_prefix=digest[:8],
        page_start=start,
        page_end=end,
        shared_only=shared_only,
    )
