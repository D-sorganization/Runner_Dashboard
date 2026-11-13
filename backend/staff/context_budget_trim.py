"""Tier-1 optional context trim (lessons, then skills) for staff dispatch (#4420)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from staff.standards_registry import estimate_tokens

if TYPE_CHECKING:
    from staff.dispatch_bundle import DispatchBundle


@dataclass
class Tier1TrimResult:
    skill_block: str
    lessons_block: str
    skill_tokens: int
    lessons_tokens: int
    trimmed: tuple[str, ...]

    @property
    def tier1_tokens(self) -> int:
        return self.skill_tokens + self.lessons_tokens

    @property
    def degraded(self) -> bool:
        return bool(self.trimmed)


def trim_tier1_optional(
    *,
    skill_block: str,
    lessons_block: str,
    tier1_cap: int,
    dispatch_bundle: DispatchBundle | None,
    truncate: Callable[[str, int], str],
) -> Tier1TrimResult:
    """Shrink optional Tier-1 blocks (lessons first, then skills) down to ``tier1_cap`` tokens."""
    skill_tokens = estimate_tokens(skill_block)
    lessons_tokens = estimate_tokens(lessons_block)
    trimmed: list[str] = []

    def _skill_pointer() -> str:
        if not dispatch_bundle:
            return ""
        pin = dispatch_bundle.skill_pin or dispatch_bundle.skill_version
        return f"\n\n(Skill package truncated; full registry pin: {pin})"

    for _ in range(8):
        if skill_tokens + lessons_tokens <= tier1_cap:
            break

        if lessons_block and lessons_tokens > 0:
            room = tier1_cap - skill_tokens
            if room <= 0:
                if lessons_block:
                    if "lessons" not in trimmed:
                        trimmed.append("lessons")
                    lessons_block = ""
                    lessons_tokens = 0
                continue
            if lessons_tokens > room:
                lessons_block = truncate(lessons_block, room)
                lessons_tokens = estimate_tokens(lessons_block)
                if "lessons" not in trimmed:
                    trimmed.append("lessons")
            continue

        if skill_block and skill_tokens > 0:
            pointer = _skill_pointer()
            pointer_tokens = estimate_tokens(pointer)
            skill_budget = max(0, tier1_cap - lessons_tokens - pointer_tokens)
            if skill_tokens > skill_budget:
                body = skill_block
                if dispatch_bundle and pointer:
                    body = truncate(skill_block, skill_budget) + pointer
                elif skill_budget <= 0:
                    body = truncate(skill_block, 0)
                else:
                    body = truncate(skill_block, skill_budget)
                if body != skill_block and "skills" not in trimmed:
                    trimmed.append("skills")
                skill_block = body
                skill_tokens = estimate_tokens(skill_block)
            continue

        break

    return Tier1TrimResult(
        skill_block=skill_block,
        lessons_block=lessons_block,
        skill_tokens=skill_tokens,
        lessons_tokens=lessons_tokens,
        trimmed=tuple(trimmed),
    )
