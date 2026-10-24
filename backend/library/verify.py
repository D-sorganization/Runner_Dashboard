"""Read-only citation and Markdown link verification for Alexandria."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import httpx
from library.citation_claims import claim_is_supported
from library.codebase_refs import REFERENCE_TOKEN, verify_codebase_citations
from library.markdown_links import reject_links_without_files, verify_links

_CITATION = re.compile(
    r"\[ref:(?P<node>[A-Za-z0-9][A-Za-z0-9.-]{0,63}):(?P<source>[a-z0-9][a-z0-9-]{0,62}):"
    r"w(?P<work>\d+):p(?P<passage>\d+)#sha256=(?P<sha>[a-fA-F0-9]{8}):p\.(?P<start>\d+)-(?P<end>\d+)\]"
    r"|lib://(?P<libsource>[a-z0-9][a-z0-9-]{0,62})/w(?P<libwork>\d+)/p(?P<libpassage>\d+)@"
    r"(?P<libsha>[a-fA-F0-9]{8})#p(?P<libstart>\d+)-(?P<libend>\d+)"
)
_LEGACY = re.compile(r"\(work_id=(?P<work>\d+)\)")
_MAX_SQLITE_INTEGER = 2**63 - 1
_MAX_FEDERATED_CITATIONS = 8
_FEDERATED_VERIFY_DEADLINE_S = 3.0
_FEDERATED_RESPONSE_LIMIT = 64_000
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_PEER_FAILURE_STATUSES = {
    "citation_not_found",
    "sha_mismatch",
    "page_range_mismatch",
    "misappropriated_reference",
    "invalid_citation",
    "node_mismatch",
    "unretrieved_reference",
}


def verification_repo_root(repo: str | None) -> Path | None:
    """Resolve a repository name only to a checkout inside configured roots."""
    if repo is None:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", repo):
        raise ValueError("repo must be a repository name")
    from staff.workspace import find_repo_checkout, repos_roots

    checkout = find_repo_checkout(repo)
    if checkout is None:
        raise LookupError("repository checkout not found")
    try:
        resolved = checkout.resolve(strict=True)
        roots = [root.resolve(strict=True) for root in repos_roots()]
    except OSError as exc:
        raise LookupError("repository checkout not found") from exc
    if not resolved.is_dir() or not any(root in resolved.parents for root in roots):
        raise LookupError("repository checkout is outside configured roots")
    return resolved


async def _fetch_peer_response(
    target: str, token: str, content: str, timeout_s: float, *, shared_only: bool = False
) -> bytes | None:
    """Fetch a bounded peer response under one wall-clock deadline."""
    chunks: list[bytes] = []
    response_size = 0
    async with asyncio.timeout(timeout_s):
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 1.0)),
            follow_redirects=False,
        ) as client:
            payload = {"content": content, "local_only": True}
            if shared_only:
                payload["shared_only"] = True
            async with client.stream(
                "POST",
                f"{target}/api/v1/library/verify",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                if response.status_code != 200:
                    return None
                async for chunk in response.aiter_bytes():
                    response_size += len(chunk)
                    if response_size > _FEDERATED_RESPONSE_LIMIT:
                        return None
                    chunks.append(chunk)
    return b"".join(chunks)


def _verify_peer_citation(
    match: re.Match[str],
    content: str,
    *,
    deadline: float,
    fleet_nodes: dict[str, str],
    expected_sha256: set[str] | None = None,
    shared_only: bool = False,
) -> dict[str, Any]:
    """Ask only the configured source node to verify its own citation."""
    from library.pool import validate_node_url

    groups = match.groupdict()
    node = groups["node"]
    source = groups["source"] or groups["libsource"]
    work_id = groups["work"] or groups["libwork"]
    passage_id = groups["passage"] or groups["libpassage"]
    page_start = groups["start"] or groups["libstart"]
    page_end = groups["end"] or groups["libend"]
    digest_prefix = (groups["sha"] or groups["libsha"]).lower()
    citation = match.group(0)
    result: dict[str, Any] = {"citation": citation[:500], "node": node, "verified": False, "status": "peer_unavailable"}
    raw_url = fleet_nodes.get(node)
    token = os.environ.get("HUB_FLEET_TOKEN", "")
    remaining = deadline - time.monotonic()
    if not raw_url or not token or remaining <= 0:
        return result
    try:
        target = validate_node_url(raw_url, fleet_nodes)
        # Keep the local claim window while removing earlier citations so the
        # peer sees exactly the citation currently being verified.
        context_prefix = content[max(0, match.start() - 500) : match.start()]
        context = _CITATION.sub(" ", context_prefix) + citation
        body = asyncio.run(_fetch_peer_response(target, token, context, min(remaining, 2.0), shared_only=shared_only))
        if body is None:
            return result
        payload = json.loads(body)
        if shared_only and (not isinstance(payload, dict) or payload.get("shared_only") is not True):
            return result
        citations = payload.get("citations") if isinstance(payload, dict) else None
        if not isinstance(citations, list) or len(citations) != 1 or not isinstance(citations[0], dict):
            return result
        remote = citations[0]
        if remote.get("citation") != citation or remote.get("node") != node:
            return result
        if remote.get("verified") is not True:
            status = remote.get("status")
            result["status"] = status if status in _PEER_FAILURE_STATUSES else "peer_unavailable"
            return result
        file_info = remote.get("file")
        passage = remote.get("passage")
        work = remote.get("work")
        if (
            not isinstance(file_info, dict)
            or file_info.get("source_id") != source
            or not isinstance(file_info.get("sha256"), str)
            or not _SHA256.fullmatch(file_info["sha256"])
            or not file_info["sha256"].startswith(digest_prefix)
            or (expected_sha256 is not None and file_info["sha256"] not in expected_sha256)
            or not isinstance(passage, dict)
            or passage.get("id") != int(passage_id)
            or passage.get("page_start") != int(page_start)
            or passage.get("page_end") != int(page_end)
            or not isinstance(work, dict)
            or work.get("id") != int(work_id)
        ):
            return result
        safe_work: dict[str, Any] = {"id": int(work_id)}
        for key in ("title", "author"):
            value = work.get(key)
            if isinstance(value, str):
                safe_work[key] = value[:200]
        year = work.get("year")
        if isinstance(year, int) and not isinstance(year, bool):
            safe_work["year"] = year
        return {
            "citation": citation[:500],
            "node": node,
            "verified": True,
            "status": "verified",
            "work": safe_work,
            "file": {"source_id": source, "sha256": file_info["sha256"]},
            "passage": {
                "id": int(passage_id),
                "page_start": int(page_start),
                "page_end": int(page_end),
            },
        }
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, OSError, RuntimeError):
        return result


def _verify_citation(
    conn: sqlite3.Connection,
    db_path: Path,
    match: re.Match[str],
    content: str,
    run_id: str | None,
    *,
    local_only: bool,
    shared_only: bool,
    deadline: float,
) -> dict[str, Any]:
    groups = match.groupdict()
    source = groups["source"] or groups["libsource"]
    result: dict[str, Any] = {"citation": match.group(0)[:500], "verified": False}
    numeric_fields = [groups[key] or groups[f"lib{key}"] for key in ("work", "passage", "start", "end")]
    if any(len(value) > 19 for value in numeric_fields):
        return {**result, "status": "invalid_citation"}
    work_id, passage_id, page_start, page_end = map(int, numeric_fields)
    if any(value < 1 or value > _MAX_SQLITE_INTEGER for value in (work_id, passage_id, page_start, page_end)):
        return {**result, "status": "invalid_citation"}
    digest_prefix = (groups["sha"] or groups["libsha"]).lower()
    from dashboard_config import FLEET_NODES, HOSTNAME

    local_node = HOSTNAME or "local"
    node = groups["node"] or local_node
    if local_only:
        result["node"] = local_node
    if node != local_node:
        if local_only:
            result.update(status="node_mismatch")
            return result
        expected_sha256 = None
        if run_id is not None:
            from library.retrieval_ledger import references_for_run

            references = references_for_run(db_path, run_id)
            identity_matches = [
                item
                for item in references
                if item["source_id"] == source
                and item["work_id"] == work_id
                and item["passage_id"] == passage_id
                and item["page_start"] == page_start
                and item["page_end"] == page_end
                and item["file_sha256"].startswith(digest_prefix)
            ]
            node_matches = [item for item in identity_matches if item["node"] == node]
            if not node_matches:
                result.update(status="node_mismatch" if identity_matches else "unretrieved_reference")
                return result
            expected_sha256 = {item["file_sha256"] for item in node_matches}
        peer_options: dict[str, Any] = {}
        if shared_only:
            peer_options["shared_only"] = True
        return _verify_peer_citation(
            match,
            content,
            deadline=deadline,
            fleet_nodes=dict(FLEET_NODES),
            expected_sha256=expected_sha256,
            **peer_options,
        )
    query = (
        "SELECT w.id,w.title,w.author,w.year,f.sha256,p.id,p.page_start,p.page_end,p.text "
        "FROM works w JOIN passages p ON p.work_id=w.id "
        "JOIN files f ON f.work_id=w.id AND f.source_id=? AND f.status='present' "
    )
    if shared_only:
        query += "JOIN library_sources s ON s.id=f.source_id "
    query += "WHERE w.id=? AND p.id=?"
    if shared_only:
        query += " AND s.shared=1 AND s.status='active'"
    row = conn.execute(query + " ORDER BY f.id LIMIT 1", (source, work_id, passage_id)).fetchone()
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
        if not any(item["node"] == node for item in matching_retrievals):
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
    db_path: Path,
    content: str,
    *,
    run_id: str | None = None,
    repo_root: Path | None = None,
    document_path: Path | None = None,
    knowledge_dir: Path | None = None,
    allow_ambient_knowledge: bool = True,
    local_only: bool = False,
    shared_only: bool = False,
) -> dict[str, Any]:
    """Verify citation tokens and relative links without modifying the library."""
    if not isinstance(content, str):
        raise ValueError("content must be text")
    if not isinstance(shared_only, bool):
        raise ValueError("shared_only must be a boolean")
    if len(content) > 1_000_000:
        raise ValueError("content must be at most 1000000 characters")
    if run_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", run_id):
        raise ValueError("invalid run_id")
    citations: list[dict[str, Any]] = []
    from dashboard_config import HOSTNAME

    db_path = Path(db_path)
    if not db_path.is_file():
        raise ValueError("library database does not exist")
    local_node = HOSTNAME or "local"
    deadline = time.monotonic() + _FEDERATED_VERIFY_DEADLINE_S
    remote_count = 0
    db_uri = f"{db_path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(db_uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for match in _CITATION.finditer(content):
            is_remote = (match.group("node") or local_node) != local_node
            if is_remote and not local_only:
                remote_count += 1
                if remote_count > _MAX_FEDERATED_CITATIONS:
                    citations.append(
                        {"citation": match.group(0)[:500], "verified": False, "status": "citation_limit_exceeded"}
                    )
                    continue
            citations.append(
                _verify_citation(
                    conn,
                    db_path,
                    match,
                    content,
                    run_id,
                    local_only=local_only,
                    shared_only=shared_only,
                    deadline=deadline,
                )
            )
    citations.extend(
        {"citation": match.group(0), "verified": False, "status": "legacy_unverifiable"}
        for match in _LEGACY.finditer(content)
    )
    if shared_only:
        citations.extend(
            {"citation": match.group(0)[:500], "verified": False, "status": "unsupported_in_shared_only"}
            for match in REFERENCE_TOKEN.finditer(content)
            if ":codebase:" in match.group(0)
        )
        links = reject_links_without_files(content)
    else:
        citations.extend(
            verify_codebase_citations(
                db_path,
                content,
                run_id,
                knowledge_dir=knowledge_dir,
                allow_ambient=allow_ambient_knowledge,
            )
        )
        links = verify_links(content, repo_root=repo_root, document_path=document_path)
    citations.extend(
        {"citation": match[0][:500], "verified": False, "status": "invalid_citation"}
        for match in REFERENCE_TOKEN.finditer(content)
        if ":codebase:" not in match[0] and _CITATION.fullmatch(match[0]) is None
    )
    response = {"verified": all(item["verified"] for item in citations + links), "citations": citations, "links": links}
    if shared_only:
        response["shared_only"] = True
    return response
