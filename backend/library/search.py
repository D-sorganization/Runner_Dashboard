"""BM25 passage search over the Alexandria library."""

from __future__ import annotations

import json
import re
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from knowledge_pack.pack import _TEXT_WEIGHT, _TITLE_WEIGHT
from library.collections import CollectionsConfig, load_collections
from library.db import LibraryStore

_MAX_FTS_QUERY_TOKENS = 32

DEFAULT_BUDGET = 1500
MAX_BUDGET = 6000
MAX_PASSAGES_PER_WORK = 2
_REPO_BOOST = 1.25
_NEAR_DUP_JACCARD = 0.85
_WORD_RE = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class SearchOptions:
    topic: str | None = None
    repo: str | None = None
    filter_source_id: str | None = None
    shared_only: bool = False


@dataclass(frozen=True)
class SearchHit:
    passage_id: int
    work_id: int
    author: str | None
    title: str | None
    year: int | None
    page_start: int
    page_end: int
    text: str
    score: float
    file_path: str | None
    token_estimate: int
    source_id: str = "default"
    file_sha256: str = ""
    work_sha256: str = ""
    node: str = ""

    @property
    def sha8(self) -> str:
        digest = self.file_sha256 or self.work_sha256 or ""
        return digest[:8]

    @property
    def citation(self) -> str:
        who, tit = self.author or "Unknown", self.title or "Untitled"
        year = f" ({self.year})" if self.year else ""
        page = self.page_start if self.page_start == self.page_end else f"{self.page_start}-{self.page_end}"
        return f"{who}{year}, {tit}, p. {page}"

    @property
    def cite_key(self) -> str:
        from dashboard_config import HOSTNAME

        node = self.node or HOSTNAME or "local"
        source = self.source_id or "default"
        pages = f"{self.page_start}-{self.page_end}"
        return f"[ref:{node}:{source}:w{self.work_id}:p{self.passage_id}#sha256={self.sha8}:p.{pages}]"

    @property
    def cite_uri(self) -> str:
        source = self.source_id or "default"
        pages = f"{self.page_start}-{self.page_end}"
        return f"lib://{source}/w{self.work_id}/p{self.passage_id}@{self.sha8}#p{pages}"


def estimate_tokens(text: str) -> int:
    words = _WORD_RE.findall(text)
    return max(1, len(words) if words else (len(text) + 3) // 4)


def sanitize_fts_query(query: str) -> str | None:
    """Reduce ``query`` to an FTS5 MATCH expression; ``None`` when no tokens remain."""
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    tokens = _WORD_RE.findall(query)[:_MAX_FTS_QUERY_TOKENS]
    if not tokens:
        return None
    return " OR ".join('"' + t.replace('"', "") + '"' for t in tokens)


def library_stats(db_path: Path) -> dict[str, Any]:
    with closing(LibraryStore(db_path).connect()) as conn:
        out: dict[str, Any] = {
            "works_active": conn.execute("SELECT COUNT(*) FROM works WHERE status='active'").fetchone()[0],
            "files_present": conn.execute("SELECT COUNT(*) FROM files WHERE status='present'").fetchone()[0],
            "passages": conn.execute("SELECT COUNT(*) FROM passages").fetchone()[0],
            "rejections": conn.execute("SELECT COUNT(*) FROM ingest_rejections").fetchone()[0],
        }
        last = conn.execute(
            "SELECT started_at, finished_at, files_indexed, source_root FROM ingest_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    out = {k: int(v) for k, v in out.items()}
    out["last_ingest"] = (
        dict(zip(("started_at", "finished_at", "files_indexed", "source_root"), last, strict=True)) if last else None
    )
    return out


def get_work(db_path: Path, work_id: int) -> dict[str, Any]:
    with closing(LibraryStore(db_path).connect()) as conn:
        row = conn.execute(
            "SELECT id, author, title, year, topics, needs_ocr FROM works WHERE id=?",
            (work_id,),
        ).fetchone()
        if row is None:
            raise LookupError("work not found")
        paths = [
            r[0]
            for r in conn.execute(
                "SELECT rel_path FROM files WHERE work_id=? AND status='present' ORDER BY id",
                (work_id,),
            ).fetchall()
        ]
    return {
        "work_id": int(row[0]),
        "author": row[1],
        "title": row[2],
        "year": row[3],
        "topics": json.loads(row[4] or "[]"),
        "needs_ocr": bool(row[5]),
        "file_paths": paths,
    }


def search_passages(
    db_path: Path,
    query: str,
    *,
    k: int = 32,
    budget_tokens: int = DEFAULT_BUDGET,
    options: SearchOptions | None = None,
    collections: CollectionsConfig | None = None,
) -> list[SearchHit]:
    if k < 1 or budget_tokens < 1 or budget_tokens > MAX_BUDGET:
        raise ValueError("invalid k or budget_tokens")
    from library.hybrid_search import hybrid_enabled, hybrid_search_passages

    # Vector expansion currently has no source visibility filter. Shared searches
    # use the filtered lexical path until vector candidates enforce that boundary.
    if hybrid_enabled() and not (options and options.shared_only):
        return hybrid_search_passages(db_path, query, k=k, budget_tokens=budget_tokens, options=options)
    ranked = bm25_ranked_passages(db_path, query, k=k, options=options, collections=collections)
    return _pack_hits(ranked, budget_tokens=budget_tokens)


def passage_hits_by_id(db_path: Path, passage_ids: list[int]) -> dict[int, SearchHit]:
    if not passage_ids:
        return {}
    placeholders = ",".join("?" for _ in passage_ids)
    sql = (
        "SELECT p.id,p.work_id,w.author,w.title,w.year,p.page_start,p.page_end,p.text,p.token_estimate,"
        " (SELECT f.rel_path FROM files f WHERE f.work_id=w.id AND f.status='present' ORDER BY f.id LIMIT 1),"
        " (SELECT f.source_id FROM files f WHERE f.work_id=w.id AND f.status='present' ORDER BY f.id LIMIT 1),"
        " (SELECT f.sha256 FROM files f WHERE f.work_id=w.id AND f.status='present' ORDER BY f.id LIMIT 1),"
        " w.sha256"
        f" FROM passages p JOIN works w ON w.id=p.work_id"
        f" WHERE p.id IN ({placeholders}) AND w.status='active'"
    )
    with closing(LibraryStore(db_path).connect()) as conn:
        rows = conn.execute(sql, passage_ids).fetchall()
    out: dict[int, SearchHit] = {}
    for row in rows:
        out[int(row[0])] = SearchHit(
            int(row[0]),
            int(row[1]),
            row[2],
            row[3],
            row[4],
            int(row[5]),
            int(row[6]),
            str(row[7]),
            0.0,
            row[9],
            int(row[8]) or estimate_tokens(str(row[7])),
            source_id=row[10] or "default",
            file_sha256=row[11] or "",
            work_sha256=row[12] or "",
        )
    return out


class AmbiguousPassageError(ValueError):
    """A short citation digest matches multiple distinct files."""


def fetch_passage(
    db_path: Path,
    *,
    source_id: str,
    work_id: int,
    passage_id: int,
    sha_prefix: str,
    page_start: int,
    page_end: int,
    shared_only: bool = False,
) -> SearchHit:
    """Resolve a cited passage against its active source and file digest."""
    if not re.fullmatch(r"[0-9a-fA-F]{8}", sha_prefix):
        raise ValueError("sha_prefix must be eight hexadecimal characters")
    if work_id < 1 or passage_id < 1 or page_start < 1 or page_end < page_start:
        raise ValueError("invalid passage reference")
    sql = (
        "SELECT p.id,p.work_id,w.author,w.title,w.year,p.page_start,p.page_end,p.text,p.token_estimate,"
        "f.rel_path,f.source_id,f.sha256,w.sha256 FROM passages p "
        "JOIN works w ON w.id=p.work_id JOIN files f ON f.work_id=w.id "
        "JOIN library_sources s ON s.id=f.source_id "
        "WHERE f.source_id=? AND p.work_id=? AND p.id=? AND p.page_start=? AND p.page_end=? "
        "AND lower(f.sha256) LIKE lower(?) AND f.status='present' AND s.status='active' AND w.status='active'"
    )
    params: list[Any] = [source_id, work_id, passage_id, page_start, page_end, f"{sha_prefix}%"]
    if shared_only:
        sql += " AND s.shared=1"
    sql += " ORDER BY f.id"
    with closing(LibraryStore(db_path).connect()) as conn:
        rows = conn.execute(sql, params).fetchall()
    if not rows:
        raise LookupError("passage reference not found")
    digests = {str(row[11]).lower() for row in rows}
    if len(digests) != 1:
        raise AmbiguousPassageError("citation digest prefix matches multiple files")
    row = rows[0]
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(row[11])) or not re.fullmatch(r"[0-9a-fA-F]{64}", str(row[12])):
        raise LookupError("passage file digest is unavailable")
    text = str(row[7]).encode("utf-8")[:2000].decode("utf-8", errors="ignore")
    return SearchHit(
        int(row[0]),
        int(row[1]),
        row[2],
        row[3],
        row[4],
        int(row[5]),
        int(row[6]),
        text,
        0.0,
        None,
        estimate_tokens(text),
        source_id=str(row[10]),
        file_sha256=str(row[11]),
        work_sha256=str(row[12]),
    )


def bm25_ranked_passages(
    db_path: Path,
    query: str,
    *,
    k: int = 32,
    options: SearchOptions | None = None,
    collections: CollectionsConfig | None = None,
) -> list[SearchHit]:
    opts = options or SearchOptions()
    cfg = collections or load_collections()
    if opts.topic:
        cfg.validate_topic(opts.topic)
    if opts.repo:
        cfg.validate_repo(opts.repo)
    match = sanitize_fts_query(query)
    if match is None:
        return []
    if k < 1:
        raise ValueError("invalid k")
    where_parts = [
        "passages_fts MATCH ?",
        "w.status='active'",
        (
            "NOT EXISTS (SELECT 1 FROM files f JOIN library_sources s ON s.id=f.source_id "
            "WHERE f.work_id=w.id AND f.status='present' AND s.status!='active')"
        ),
    ]
    params: list[Any] = [match]
    if opts.filter_source_id:
        where_parts.append(
            "EXISTS (SELECT 1 FROM files f WHERE f.work_id=w.id AND f.status='present' AND f.source_id=?)"
        )
        params.append(opts.filter_source_id)
    if opts.shared_only:
        where_parts.append(
            "EXISTS (SELECT 1 FROM files f JOIN library_sources s ON s.id=f.source_id "
            "WHERE f.work_id=w.id AND f.status='present' AND s.shared=1)"
        )
    where_sql = " AND ".join(where_parts)
    params.append(k * 4)

    src_sub = "(SELECT f.source_id FROM files f WHERE f.work_id=w.id AND f.status='present' "
    if opts.shared_only:
        src_sub += "AND source_id IN (SELECT id FROM library_sources WHERE shared=1 AND status='active') "
    if opts.filter_source_id:
        src_sub += f"AND f.source_id='{opts.filter_source_id}' "
    rel_sub = src_sub.replace("f.source_id", "f.rel_path") + "ORDER BY f.id LIMIT 1)"
    sha_sub = src_sub.replace("f.source_id", "f.sha256") + "ORDER BY f.id LIMIT 1)"
    src_sub += "ORDER BY f.id LIMIT 1)"

    sql = (
        "SELECT p.id,p.work_id,w.author,w.title,w.year,p.page_start,p.page_end,p.text,p.token_estimate,w.topics,"
        f" {rel_sub},"
        f" bm25(passages_fts,{_TITLE_WEIGHT},{_TEXT_WEIGHT}),"
        f" {src_sub},"
        f" {sha_sub},"
        " w.sha256"
        " FROM passages_fts JOIN passages p ON p.id=passages_fts.rowid"
        " JOIN works w ON w.id=p.work_id"
        f" WHERE {where_sql}"
        " ORDER BY 10,p.id LIMIT ?"
    )
    with closing(LibraryStore(db_path).connect()) as conn:
        rows = conn.execute(sql, params).fetchall()
    ranked: list[SearchHit] = []
    for row in rows:
        topics = json.loads(row[9] or "[]")
        if opts.topic and opts.topic not in topics:
            continue
        score = -float(row[11])
        if opts.repo and opts.topic is None:
            if set(topics) & set(cfg.repo_topics(opts.repo)):
                score *= _REPO_BOOST
        elif opts.repo:
            if opts.topic in cfg.repo_topics(opts.repo):
                score *= _REPO_BOOST
        ranked.append(
            SearchHit(
                int(row[0]),
                int(row[1]),
                row[2],
                row[3],
                row[4],
                int(row[5]),
                int(row[6]),
                str(row[7]),
                score,
                row[10],
                int(row[8]) or estimate_tokens(str(row[7])),
                source_id=row[12] or "default",
                file_sha256=row[13] or "",
                work_sha256=row[14] or "",
            )
        )
    ranked.sort(key=lambda h: h.score, reverse=True)
    return ranked


def _pack_hits(ranked: list[SearchHit], *, budget_tokens: int) -> list[SearchHit]:
    hits: list[SearchHit] = []
    used = 0
    per_work: dict[int, int] = {}
    seen_texts: list[set[str]] = []
    for hit in ranked:
        if per_work.get(hit.work_id, 0) >= MAX_PASSAGES_PER_WORK:
            continue
        words = set(_WORD_RE.findall(hit.text.lower()))
        if words and any(_jaccard(words, prev) >= _NEAR_DUP_JACCARD for prev in seen_texts):
            continue
        tokens = hit.token_estimate
        if used and used + tokens > budget_tokens:
            break
        if used + tokens > budget_tokens:
            continue
        used += tokens
        per_work[hit.work_id] = per_work.get(hit.work_id, 0) + 1
        seen_texts.append(words)
        hits.append(hit)
    return hits


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0
