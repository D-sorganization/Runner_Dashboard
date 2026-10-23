"""Read-only citation and Markdown link verification for Alexandria."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from library.citation_claims import claim_is_supported
from library.codebase_refs import verify_codebase_citations

_CITATION = re.compile(
    r"\[ref:(?P<node>[A-Za-z0-9][A-Za-z0-9.-]{0,63}):(?P<source>[a-z0-9][a-z0-9-]{0,62}):"
    r"w(?P<work>\d+):p(?P<passage>\d+)#sha256=(?P<sha>[a-fA-F0-9]{8}):p\.(?P<start>\d+)-(?P<end>\d+)\]"
    r"|lib://(?P<libsource>[a-z0-9][a-z0-9-]{0,62})/w(?P<libwork>\d+)/p(?P<libpassage>\d+)@"
    r"(?P<libsha>[a-fA-F0-9]{8})#p(?P<libstart>\d+)-(?P<libend>\d+)"
)
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\((?P<url>[^)]+)\)")
_LEGACY = re.compile(r"\(work_id=(?P<work>\d+)\)")


def _anchors(markdown: str) -> set[str]:
    headings = re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", markdown, re.MULTILINE)
    return {re.sub(r"[^\w-]", "", re.sub(r"\s+", "-", re.sub(r"[`*_]", "", heading.lower()))) for heading in headings}


def _verify_citation(
    conn: sqlite3.Connection, match: re.Match[str], content: str, run_id: str | None
) -> dict[str, Any]:
    groups = match.groupdict()
    source = groups["source"] or groups["libsource"]
    work_id = int(groups["work"] or groups["libwork"])
    passage_id = int(groups["passage"] or groups["libpassage"])
    digest_prefix = (groups["sha"] or groups["libsha"]).lower()
    page_start = int(groups["start"] or groups["libstart"])
    page_end = int(groups["end"] or groups["libend"])
    row = conn.execute(
        "SELECT w.id,w.title,w.author,w.year,f.sha256,p.id,p.page_start,p.page_end,p.text "
        "FROM works w JOIN passages p ON p.work_id=w.id "
        "JOIN files f ON f.work_id=w.id AND f.source_id=? AND f.status='present' "
        "WHERE w.id=? AND p.id=? ORDER BY f.id LIMIT 1",
        (source, work_id, passage_id),
    ).fetchone()
    result: dict[str, Any] = {"citation": match.group(0), "verified": False}
    if row is None:
        result.update(status="citation_not_found")
        return result
    file_hash = str(row[4]).lower()
    if not file_hash.startswith(digest_prefix):
        result.update(status="sha_mismatch")
        return result
    if page_start != row[6] or page_end != row[7]:
        result.update(status="page_range_mismatch")
        return result
    if run_id is not None:
        from library.retrieval_ledger import references_for_run

        run_references = references_for_run(Path(conn.execute("PRAGMA database_list").fetchone()[2]), run_id)
        matching_retrievals = [
            item
            for item in run_references
            if item["source_id"] == source
            and item["work_id"] == work_id
            and item["passage_id"] == passage_id
            and item["file_sha256"] == file_hash
            and item["page_start"] == page_start
            and item["page_end"] == page_end
        ]
        if not any(item["node"] == groups["node"] for item in matching_retrievals):
            result.update(status="node_mismatch" if matching_retrievals else "unretrieved_reference")
            return result
    # Check the bounded sentence immediately preceding the citation. Never return passage text.
    if not claim_is_supported(content, match.start(), str(row[8])):
        result.update(status="misappropriated_reference")
        return result
    result.update(
        verified=True,
        status="verified",
        work={"id": row[0], "title": row[1], "author": row[2], "year": row[3]},
        file={"sha256": file_hash, "source_id": source},
        passage={"id": row[5], "page_start": row[6], "page_end": row[7]},
    )
    return result


def verify_content(
    db_path: Path, content: str, *, run_id: str | None = None, repo_root: Path | None = None
) -> dict[str, Any]:
    """Verify citation tokens and relative links without modifying the library."""
    if not isinstance(content, str):
        raise ValueError("content must be text")
    if len(content) > 1_000_000:
        raise ValueError("content must be at most 1000000 characters")
    if run_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", run_id):
        raise ValueError("invalid run_id")
    citations: list[dict[str, Any]] = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for match in _CITATION.finditer(content):
            citations.append(_verify_citation(conn, match, content, run_id))
    citations.extend(
        {"citation": match.group(0), "verified": False, "status": "legacy_unverifiable"}
        for match in _LEGACY.finditer(content)
    )
    citations.extend(verify_codebase_citations(db_path, content, run_id))
    links: list[dict[str, Any]] = []
    for match in _LINK.finditer(content):
        url = match.group("url").strip()
        if url.startswith(("https://", "http://", "mailto:")):
            links.append({"url": url, "verified": True, "status": "external_unchecked"})
            continue
        path_text, _, anchor = url.partition("#")
        if not path_text or repo_root is None:
            links.append({"url": url, "verified": False, "status": "broken_link"})
            continue
        root = repo_root.resolve()
        target = (root / unquote(path_text)).resolve()
        if root not in target.parents and target != root or not target.is_file():
            links.append({"url": url, "verified": False, "status": "broken_link"})
            continue
        if anchor:
            try:
                markdown = target.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                links.append({"url": url, "verified": False, "status": "broken_link"})
                continue
            if unquote(anchor) not in _anchors(markdown):
                links.append({"url": url, "verified": False, "status": "broken_link"})
                continue
        links.append({"url": url, "verified": True, "status": "verified"})
    return {"verified": all(item["verified"] for item in citations + links), "citations": citations, "links": links}
