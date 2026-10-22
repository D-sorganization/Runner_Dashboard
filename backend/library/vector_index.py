"""sqlite-vec passage index for Alexandria (#2165 slice 7)."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import sqlite_vec
from library.db import LibraryStore, init_library_db
from library.embeddings import EMBED_DIM, passage_embedder

log = logging.getLogger("dashboard.library.vector_index")


def _connect_vec(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.row_factory = sqlite3.Row
    return conn


def load_sqlite_vec(conn: sqlite3.Connection) -> None:
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)


def ensure_vector_schema(conn: sqlite3.Connection, *, dim: int = EMBED_DIM) -> None:
    load_sqlite_vec(conn)
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS passage_vec USING vec0("
        f"passage_id INTEGER PRIMARY KEY, embedding float[{dim}])"
    )


def backfill_passage_embeddings(
    conn: sqlite3.Connection,
    *,
    embed_passage: Callable[[int, str], list[float]] | None = None,
    batch_size: int = 32,
    dim: int = EMBED_DIM,
) -> int:
    ensure_vector_schema(conn, dim=dim)
    embed = embed_passage or passage_embedder()
    rows = conn.execute(
        "SELECT p.id, p.text FROM passages p "
        "LEFT JOIN passage_vec v ON v.passage_id = p.id WHERE v.passage_id IS NULL ORDER BY p.id"
    ).fetchall()
    written = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        for row in batch:
            pid, text = int(row[0]), str(row[1])
            vec = embed(pid, text)
            if len(vec) != dim:
                raise ValueError(f"embedding dim {len(vec)} != {dim}")
            conn.execute(
                "INSERT OR REPLACE INTO passage_vec(passage_id, embedding) VALUES (?, ?)",
                (pid, sqlite_vec.serialize_float32(vec)),
            )
            written += 1
    return written


def vector_passage_ids(db_path: Path, query_vec: list[float], *, k: int = 32) -> list[int]:
    init_library_db(db_path)
    with closing(_connect_vec(db_path)) as conn:
        ensure_vector_schema(conn, dim=len(query_vec))
        rows = conn.execute(
            "SELECT passage_id, distance FROM passage_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (sqlite_vec.serialize_float32(query_vec), k),
        ).fetchall()
    return [int(r[0]) for r in rows]


def backfill_db(db_path: Path, *, batch_size: int = 32) -> int:
    init_library_db(db_path)
    with closing(LibraryStore(db_path).connect()) as conn:
        count = backfill_passage_embeddings(conn, batch_size=batch_size)
        conn.commit()
    return count
