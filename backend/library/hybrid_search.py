"""BM25 + vector hybrid search behind LIBRARY_HYBRID_SEARCH (#2165 slice 7)."""

from __future__ import annotations

import os
from pathlib import Path

from library.embeddings import query_embedding
from library.rrf import reciprocal_rank_fusion
from library.search import SearchHit, SearchOptions, _pack_hits, bm25_ranked_passages, passage_hits_by_id
from library.vector_index import vector_passage_ids


def hybrid_enabled() -> bool:
    return os.environ.get("LIBRARY_HYBRID_SEARCH", "").strip().lower() in {"1", "true", "yes", "on"}


def hybrid_search_passages(
    db_path: Path,
    query: str,
    *,
    k: int,
    budget_tokens: int,
    options: SearchOptions | None,
) -> list[SearchHit]:
    bm25 = bm25_ranked_passages(db_path, query, k=k, options=options)
    if not bm25:
        return []
    try:
        qvec = query_embedding(query)
        vec_ids = vector_passage_ids(db_path, qvec, k=k)
    except Exception:
        return _pack_hits(bm25, budget_tokens=budget_tokens)
    bm25_ids = [h.passage_id for h in bm25]
    fused_ids = reciprocal_rank_fusion(bm25_ids, vec_ids, weights=(1.0, 1.5))
    by_id = {h.passage_id: h for h in bm25}
    missing = [pid for pid in fused_ids if pid not in by_id]
    if missing:
        by_id.update(passage_hits_by_id(db_path, missing))
    ordered = [by_id[pid] for pid in fused_ids if pid in by_id]
    return _pack_hits(ordered, budget_tokens=budget_tokens)
