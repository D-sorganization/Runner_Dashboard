"""Estimator and budgets for staff injected prompt context (#2220, epic #2219)."""

from __future__ import annotations

ESTIMATOR_VERSION = "char4_utf8_v1"

TIER_0_TOKEN_BUDGET = 1500
TIER_1_TOKEN_BUDGET = 1000
LESSONS_TOKEN_BUDGET = 600
AGGREGATE_TOKEN_CAP = 2200


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate: 4 UTF-8 bytes per token (ceiling)."""
    if not text:
        return 0
    return (len(text.encode("utf-8")) + 3) // 4
