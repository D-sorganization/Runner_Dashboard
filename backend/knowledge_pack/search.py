# Vendored from Tools (commit 6f064e6091b8fc0f0e5390913602a747ac544fb1)
# Path in Tools: src/shared/python/ai/knowledge/search.py
# Part of D-sorganization/Repository_Management#1772 (K0 / K2, Runner_Dashboard#1479)
# fmt: off
"""SQLite-backed KnowledgePack search implementations."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from contextlib import closing
from typing import TYPE_CHECKING

from .pack import (
    Embedder,
    Passage,
    _as_embed_fn,
    _hidden_clause,
    _passage,
    blob_to_embedding,
    cosine_similarity,
    get_minilm_embedder,
    reciprocal_rank_fusion,
)

if TYPE_CHECKING:
    from .pack import KnowledgePack

_logger = logging.getLogger(__name__)
_TITLE_WEIGHT = 5.0
_TEXT_WEIGHT = 1.0
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_MAX_QUERY_TOKENS = 32


def _search_pack(
    pack: KnowledgePack,
    query: str,
    k: int,
    include_superseded: bool,
    embedder: Embedder | Callable[[str], Sequence[float]] | None,
    hybrid: bool,
    repo: str | None = None,
) -> list[Passage]:
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    if not isinstance(k, int) or isinstance(k, bool) or k < 1:
        raise ValueError("k must be a positive integer")
    if repo is not None and not isinstance(repo, str):
        raise TypeError("repo must be a string or None")
    if pack.has_embeddings and hybrid:
        return _search_hybrid(pack, query, k, include_superseded, embedder, repo)
    return _search_bm25(pack, query, k, include_superseded, repo)


def search_repo(
    pack: KnowledgePack,
    query: str,
    repo: str,
    *,
    k: int = 8,
    include_superseded: bool = False,
    embedder: Embedder | Callable[[str], Sequence[float]] | None = None,
    hybrid: bool = True,
) -> list[Passage]:
    """Search a knowledge pack within one repo before applying the result limit."""
    return _search_pack(pack, query, k, include_superseded, embedder, hybrid, repo)


def _search_bm25(
    pack: KnowledgePack,
    query: str,
    k: int,
    include_superseded: bool,
    repo: str | None = None,
) -> list[Passage]:
    tokens = _TOKEN_RE.findall(query)[:_MAX_QUERY_TOKENS]
    if not tokens:
        return []
    match = " OR ".join('"' + t.replace('"', "") + '"' for t in tokens)
    hidden = "" if include_superseded else _hidden_clause()
    repo_filter = " AND p.repo = ?" if repo is not None else ""
    sql = (
        "SELECT p.repo, p.source, p.anchor, p.title, p.text, p.commit_sha,"
        " p.content_hash, p.status, p.authority,"
        f" bm25(passages_fts, {_TITLE_WEIGHT}, {_TEXT_WEIGHT}) AS score"
        " FROM passages_fts JOIN passages p ON p.id = passages_fts.rowid"
        f" WHERE passages_fts MATCH ?{hidden}{repo_filter}"
        " ORDER BY score, p.authority_rank, p.id LIMIT ?"
    )
    parameters = (match, repo, k) if repo is not None else (match, k)
    with closing(pack._connect()) as conn:
        rows = conn.execute(sql, parameters).fetchall()
    return [_passage(row) for row in rows]


def _search_hybrid(
    pack: KnowledgePack,
    query: str,
    k: int,
    include_superseded: bool,
    embedder: Embedder | Callable[[str], Sequence[float]] | None = None,
    repo: str | None = None,
) -> list[Passage]:
    if embedder is None:
        embedder = get_minilm_embedder()
    if embedder is None:
        _logger.warning(
            "Pack has embeddings but no MiniLM embedder is available;"
            " falling back to plain BM25 ranking."
        )
        return _search_bm25(
            pack, query, k=k, include_superseded=include_superseded, repo=repo
        )
    emb_fn = _as_embed_fn(embedder)
    query_vec = emb_fn(query)

    candidate_pool_size = max(k * 5, 50)
    tokens = _TOKEN_RE.findall(query)[:_MAX_QUERY_TOKENS]
    bm25_ids: list[int] = []
    hidden = "" if include_superseded else _hidden_clause()
    repo_filter = " AND p.repo = ?" if repo is not None else ""

    with closing(pack._connect()) as conn:
        if tokens:
            match = " OR ".join('"' + t.replace('"', "") + '"' for t in tokens)
            bm25_sql = (
                "SELECT p.id FROM passages_fts JOIN passages p"
                " ON p.id = passages_fts.rowid"
                f" WHERE passages_fts MATCH ?{hidden}{repo_filter}"
                f" ORDER BY bm25(passages_fts, {_TITLE_WEIGHT}, {_TEXT_WEIGHT}),"
                " p.authority_rank, p.id LIMIT ?"
            )
            bm25_parameters = (
                (match, repo, candidate_pool_size)
                if repo is not None
                else (match, candidate_pool_size)
            )
            bm25_ids = [
                row[0] for row in conn.execute(bm25_sql, bm25_parameters).fetchall()
            ]

        bm25_ranks = {p_id: rank for rank, p_id in enumerate(bm25_ids, start=1)}
        emb_sql = (
            "SELECT p.id, p.embedding FROM passages p"
            f" WHERE p.embedding IS NOT NULL{hidden}{repo_filter}"
        )
        embedding_parameters = (repo,) if repo is not None else ()
        scored_candidates = sorted(
            [
                (cosine_similarity(query_vec, blob_to_embedding(blob)), p_id)
                for p_id, blob in conn.execute(emb_sql, embedding_parameters).fetchall()
            ],
            reverse=True,
        )[:candidate_pool_size]
        cosine_ranks = {
            p_id: rank for rank, (_, p_id) in enumerate(scored_candidates, start=1)
        }

        fused_scores = reciprocal_rank_fusion(bm25_ranks, cosine_ranks)
        if not fused_scores:
            return []

        target_ids = sorted(fused_scores.keys(), key=lambda pid: -fused_scores[pid])[
            : k * 2
        ]

        placeholders = ",".join("?" for _ in target_ids)
        fetch_sql = (
            "SELECT p.repo, p.source, p.anchor, p.title, p.text, p.commit_sha,"
            " p.content_hash, p.status, p.authority, p.authority_rank, p.id"
            f" FROM passages p WHERE p.id IN ({placeholders}){repo_filter}"
        )
        fetch_parameters = (*target_ids, repo) if repo is not None else target_ids
        rows = conn.execute(fetch_sql, fetch_parameters).fetchall()

    passages: list[tuple[float, int, int, Passage]] = [
        (
            fused_scores.get(int(r[10]), 0.0),
            int(r[9]),
            int(r[10]),
            _passage(r, fused_scores.get(int(r[10]), 0.0)),
        )
        for r in rows
    ]
    passages.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [item[3] for item in passages[:k]]

# fmt: on
