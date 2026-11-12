"""Seat provider fallback chains for board and panel read-only turns (#3528).

Each named seat tries a ordered list of (provider, model) steps until one turn succeeds.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import provider_switch
from staff.adapters import ADAPTERS
from staff.group_models import SeatSpec
from staff.panel_models import PanelSpeaker, TurnOutcome

TurnRunner = Callable[[PanelSpeaker, str, str, str | None], Awaitable[TurnOutcome]]

# Fable: Cursor subscription models first, then agy Opus, then Codex.
FABLE_FALLBACK: tuple[tuple[str, str | None], ...] = (
    ("cursor-agent", "claude-fable-5-1"),
    ("antigravity", "claude-opus-5-5"),
    ("codex", None),
)

DEFAULT_SEAT_FALLBACK: tuple[tuple[str, str | None], ...] = (
    ("claude", None),
    ("codex", None),
    ("antigravity", None),
)

_SEAT_CHAINS: dict[str, tuple[tuple[str, str | None], ...]] = {
    "fable": FABLE_FALLBACK,
    "alpha": FABLE_FALLBACK,
}


@dataclass(frozen=True)
class FallbackStep:
    provider: str
    model: str | None


def chain_for_seat_name(seat_name: str) -> tuple[FallbackStep, ...]:
    key = seat_name.strip().lower().split()[0]
    raw = _SEAT_CHAINS.get(key, DEFAULT_SEAT_FALLBACK)
    return tuple(FallbackStep(p, m) for p, m in raw)


def _usable(provider: str) -> bool:
    pid = provider.lower().strip()
    return pid in ADAPTERS and not provider_switch.is_disabled(pid)


def steps_for_speaker(speaker: PanelSpeaker) -> tuple[FallbackStep, ...]:
    name_key = speaker.name.strip().lower()
    if name_key.startswith("fable") or name_key == "alpha" or "fable" in name_key:
        raw: Sequence[tuple[str, str | None]] = ((speaker.provider, speaker.model), *FABLE_FALLBACK)
    else:
        raw = ((speaker.provider, speaker.model), *DEFAULT_SEAT_FALLBACK)
    out: list[FallbackStep] = []
    seen: set[tuple[str, str | None]] = set()
    for provider, model in raw:
        key = (provider, model)
        if key in seen or not _usable(provider):
            continue
        seen.add(key)
        out.append(FallbackStep(provider, model))
    return tuple(out)


def steps_for_board_seat(seat: SeatSpec) -> tuple[FallbackStep, ...]:
    title_key = (seat.title or seat.name).split("—")[0].split("-")[-1].strip().lower()
    if "fable" in title_key or seat.name == "alpha":
        raw = ((seat.provider, seat.model if seat.pin_model else None), *FABLE_FALLBACK)
    else:
        raw = ((seat.provider, seat.model if seat.pin_model else None), *DEFAULT_SEAT_FALLBACK)
    out: list[FallbackStep] = []
    seen: set[tuple[str, str | None]] = set()
    for provider, model in raw:
        key = (provider, model)
        if key in seen or not _usable(provider):
            continue
        seen.add(key)
        out.append(FallbackStep(provider, model))
    return tuple(out)


async def run_turn_with_fallback(
    speaker: PanelSpeaker,
    prompt: str,
    thread_id: str,
    message_id: str | None,
    runner: TurnRunner,
    *,
    steps: Sequence[FallbackStep] | None = None,
) -> TurnOutcome:
    """Try each fallback step until a turn succeeds; last error is returned if all fail."""
    chain = steps if steps is not None else steps_for_speaker(speaker)
    last_error = "no usable providers"
    for step in chain:
        trial = PanelSpeaker(
            name=speaker.name,
            perspective=speaker.perspective,
            provider=step.provider,
            model=step.model,
        )
        outcome = await runner(trial, prompt, thread_id, message_id)
        if outcome.ok:
            if step.provider != speaker.provider or step.model != speaker.model:
                note = f"(degraded to {step.provider}"
                if step.model:
                    note += f" {step.model}"
                note += ")"
                return TurnOutcome(ok=True, text=f"{outcome.text}\n\n{note}")
            return outcome
        last_error = outcome.error or last_error
    return TurnOutcome(ok=False, error=last_error)
