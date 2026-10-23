"""Reciprocal rank fusion for hybrid library search (#2165 slice 7)."""

from __future__ import annotations

from collections import defaultdict


def reciprocal_rank_fusion(
    *ranked_ids: list[int],
    k: int = 60,
    weights: tuple[float, ...] | None = None,
) -> list[int]:
    """Fuse ordered passage-id lists with RRF; higher score ranks first."""
    scores: dict[int, float] = defaultdict(float)
    for i, listing in enumerate(ranked_ids):
        weight = 1.0
        if weights is not None and i < len(weights):
            weight = weights[i]
        for rank, passage_id in enumerate(listing, start=1):
            scores[int(passage_id)] += weight / (k + rank)
    return sorted(scores, key=lambda pid: scores[pid], reverse=True)
