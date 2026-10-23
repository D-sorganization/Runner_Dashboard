from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

import pytest
from library.db import LibraryStore, init_library_db
from library.embeddings import deterministic_embedding_for_text
from library.rrf import reciprocal_rank_fusion
from library.search import search_passages
from library.vector_index import backfill_passage_embeddings, ensure_vector_schema, vector_passage_ids


def _seed_passages(db: Path, passages: list[tuple[str, str]]) -> list[int]:
    """Insert one work and passages; sync FTS. Returns passage ids."""
    init_library_db(db)
    with closing(LibraryStore(db).connect()) as conn:
        conn.execute(
            "INSERT INTO works (sha256, author, title, year, format, topics, status) VALUES (?,?,?,?,?,?,?)",
            ("abc", "Author", "Title", 2020, "pdf", json.dumps(["Golf"]), "active"),
        )
        work_id = int(conn.execute("SELECT id FROM works").fetchone()[0])
        ids: list[int] = []
        for page, text in enumerate(passages, start=1):
            conn.execute(
                "INSERT INTO passages (work_id, page_start, page_end, text, token_estimate) VALUES (?,?,?,?,?)",
                (work_id, page, page, text, max(1, len(text.split()))),
            )
            pid = int(conn.execute("SELECT id FROM passages ORDER BY id DESC").fetchone()[0])
            conn.execute(
                "INSERT INTO passages_fts (rowid, title, text) VALUES (?, ?, ?)",
                (pid, "Title", text),
            )
            ids.append(pid)
        conn.commit()
    return ids


def test_reciprocal_rank_fusion_prefers_overlap() -> None:
    fused = reciprocal_rank_fusion([10, 20, 30], [30, 10, 40], k=60)
    assert 30 in fused[:2]
    assert 10 in fused[:2]


@pytest.mark.parametrize("k", [1, 2, 5])
def test_real_vector_query_orders_and_bounds_neighbors(tmp_path: Path, k: int) -> None:
    db = tmp_path / "neighbors.sqlite"
    ids = _seed_passages(db, ["nearest", "middle", "farthest"])
    vectors = {pid: [float(rank), 0.0] for rank, pid in enumerate(ids)}
    with closing(LibraryStore(db).connect()) as conn:
        backfill_passage_embeddings(conn, embed_passage=lambda pid, text: vectors[pid], dim=2)
        conn.commit()
    assert vector_passage_ids(db, [0.1, 0.0], k=k) == ids[:k]


def test_hybrid_beats_bm25_on_labelled_queries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "lib.sqlite"
    p_golf, p_noise = _seed_passages(
        db,
        [
            "driver wrist cock and club release timing",
            "hip flexor strengthening clinical rehabilitation protocol",
        ],
    )
    labelled = [
        {
            "query": "golf hip torque downswing",
            "relevant": p_golf,
            "query_vec": deterministic_embedding_for_text("golf hip torque downswing"),
            "passage_vecs": {
                p_golf: deterministic_embedding_for_text("golf hip torque biomechanics downswing"),
                p_noise: deterministic_embedding_for_text("clinical rehab flexor"),
            },
        }
    ]

    def _passage_vec(passage_id: int, text: str) -> list[float]:
        for row in labelled:
            if passage_id in row["passage_vecs"]:
                return row["passage_vecs"][passage_id]
        return deterministic_embedding_for_text(text)

    def _query_vec(query: str) -> list[float]:
        for row in labelled:
            if row["query"] == query:
                return row["query_vec"]
        return deterministic_embedding_for_text(query)

    monkeypatch.setenv("LIBRARY_EMBED_PROVIDER", "test")
    monkeypatch.setenv("LIBRARY_HYBRID_SEARCH", "0")
    bm25_top = search_passages(db, labelled[0]["query"], k=8, budget_tokens=6000)
    assert bm25_top and bm25_top[0].passage_id == p_noise

    with closing(LibraryStore(db).connect()) as conn:
        ensure_vector_schema(conn)
        backfill_passage_embeddings(conn, embed_passage=_passage_vec, batch_size=8)
        conn.commit()

    monkeypatch.setenv("LIBRARY_HYBRID_SEARCH", "1")
    monkeypatch.setattr("library.hybrid_search.query_embedding", _query_vec)
    monkeypatch.setattr(
        "library.hybrid_search.vector_passage_ids",
        lambda _db, _qv, k=32: [p_golf],
    )
    hybrid_top = search_passages(db, labelled[0]["query"], k=8, budget_tokens=6000)
    assert hybrid_top[0].passage_id == p_golf

    vec_only = vector_passage_ids(db, _query_vec(labelled[0]["query"]), k=5)
    assert vec_only[0] == p_golf


def test_hybrid_flag_off_ignores_vectors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "lib.sqlite"
    ids = _seed_passages(db, ["alpha biomechanics", "beta recipes"])
    monkeypatch.setenv("LIBRARY_EMBED_PROVIDER", "test")
    with closing(LibraryStore(db).connect()) as conn:
        ensure_vector_schema(conn)
        backfill_passage_embeddings(
            conn, embed_passage=lambda pid, text: deterministic_embedding_for_text(text), batch_size=8
        )
        conn.commit()
    monkeypatch.setenv("LIBRARY_HYBRID_SEARCH", "0")
    hits = search_passages(db, "alpha", k=5, budget_tokens=2000)
    assert hits[0].passage_id == ids[0]
