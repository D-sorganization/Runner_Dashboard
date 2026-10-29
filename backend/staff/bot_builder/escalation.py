"""Cumulative escalation diff vs previous and original published versions."""

from __future__ import annotations

from dataclasses import dataclass

from staff.bot_builder.schedules import schedule_or_trigger_widening_reasons
from staff.bot_builder.schema import BuilderRoleDefinition


@dataclass(frozen=True)
class EscalationDiff:
    newly_able_vs_previous: tuple[str, ...]
    newly_able_vs_original: tuple[str, ...]
    requires_confirmation: tuple[str, ...]


def _cap_delta(baseline: BuilderRoleDefinition, candidate: BuilderRoleDefinition) -> tuple[str, ...]:
    base = set(baseline.capabilities)
    new = [c for c in candidate.capabilities if c not in base]
    return tuple(sorted(new))


def _norm(text: str) -> str:
    return " ".join(text.split())


def escalation_diff(
    original: BuilderRoleDefinition,
    previous: BuilderRoleDefinition,
    candidate: BuilderRoleDefinition,
) -> EscalationDiff:
    """Compare candidate to previous and original; flag persona/instruction/schedule/trigger widening."""
    confirm: list[str] = []
    if _norm(previous.persona) != _norm(candidate.persona):
        confirm.append("persona")
    if _norm(previous.instructions) != _norm(candidate.instructions):
        confirm.append("instructions")
    confirm.extend(schedule_or_trigger_widening_reasons(previous, candidate))

    return EscalationDiff(
        newly_able_vs_previous=_cap_delta(previous, candidate),
        newly_able_vs_original=_cap_delta(original, candidate),
        requires_confirmation=tuple(confirm),
    )
