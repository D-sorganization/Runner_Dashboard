"""Bound gathered Barb reference context without clipping the user's current message."""

from __future__ import annotations

from collections.abc import Sequence

from staff.standards_registry import TOTAL_CONTEXT_TOKEN_CAP, estimate_tokens


def cap_chat_context(blocks: Sequence[str | None], budget: int = TOTAL_CONTEXT_TOKEN_CAP) -> tuple[str | None, ...]:
    """Keep reference blocks in order within the shared token estimate ceiling."""
    remaining = max(0, budget - max(0, min(len(blocks), 3) - 1))
    result: list[str | None] = []
    for index, block in enumerate(blocks):
        if not block or not block.strip() or remaining <= 0:
            result.append(None)
            continue
        text = block.strip()
        # Reserve room for all three required blocks: Tier 0, retrieval, and continuity.
        allocation = min(remaining, (1000, 600, 600)[index]) if index < 3 else remaining
        if estimate_tokens(text) > allocation:
            text = text[: allocation * 4]
            text = text.rsplit(" ", 1)[0] if " " in text else text
        used = estimate_tokens(text)
        result.append(text if used else None)
        remaining -= used
    return tuple(result)
