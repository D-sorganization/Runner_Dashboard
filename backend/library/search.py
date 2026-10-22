"""BM25 passage search over the Alexandria library."""

from __future__ import annotations

import re
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from knowledge_pack.pack import _TEXT_WEIGHT, _TITLE_WEIGHT, sanitize_fts_query
from library.db import LibraryStore

DEFAULT_BUDGET = 1500
MAX_BUDGET = 6000
_WORD_RE = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class SearchHit:
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

    @property
    def citation(self) -> str:
        who, tit = self.author or "Unknown", self.title or "Untitled"
        year = f" ({self.year})" if self.year else ""
        page = self.page_start if self.page_start == self.page_end else f"{self.page_start}-{self.page_end}"
        return f"{who}{year}, {tit}, p. {page}"


def estimate_tokens(text: str) -> int:
    words = _WORD_RE.findall(text)
    return max(1, len(words) if words else (len(text) + 3) // 4)


def search_passages(
    db_path: Path, query: str, *, k: int = 32, budget_tokens: int = DEFAULT_BUDGET
) -> list[SearchHit]:
    match = sanitize_fts_query(query)
    if match is None:
        return []
    if k < 1 or budget_tokens < 1 or budget_tokens > MAX_BUDGET:
        raise ValueError("invalid k or budget_tokens")
    sql = (
        "SELECT p.work_id,w.author,w.title,w.year,p.page_start,p.page_end,p.text,p.token_estimate,"
        " (SELECT rel_path FROM files WHERE work_id=w.id AND status='present' ORDER BY id LIMIT 1),"
        f" bm25(passages_fts,{_TITLE_WEIGHT},{_TEXT_WEIGHT})"
        " FROM passages_fts JOIN passages p ON p.id=passages_fts.rowid JOIN works w ON w.id=p.work_id"
        " WHERE passages_fts MATCH ? AND w.status='active' ORDER BY 9,p.id LIMIT ?"
    )
    with closing(LibraryStore(db_path).connect()) as conn:
        rows = conn.execute(sql, (match, k)).fetchall()
    hits: list[SearchHit] = []
    used = 0
    for row in rows:
        tokens = int(row[7]) or estimate_tokens(str(row[6]))
        if used and used + tokens > budget_tokens:
            break
        if used + tokens > budget_tokens:
            continue
        used += tokens
        hits.append(
            SearchHit(
                int(row[0]), row[1], row[2], row[3], int(row[4]), int(row[5]), str(row[6]),
                -float(row[9]), row[8], tokens,
            )
        )
    return hits
