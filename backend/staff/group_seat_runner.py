"""Runner for Board group-thread seats via real model turns (#1637).

Board seats run concurrently as read-only CLI chat turns without streaming tokens
into the thread bus. Each seat receives its persona/mandate and the owner's question,
answers with a one-line position first, and reports actual cost based on token counts.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from staff.group_models import SeatReply, SeatSpec, lookup_seat_price
from staff.panel import default_turn_runner
from staff.panel_models import PanelSpeaker, TurnOutcome

__all__ = [
    "run_seat",
    "seat_cost_usd",
    "seat_prompt",
]


def seat_prompt(seat: SeatSpec, prompt: str) -> str:
    """Construct a read-only deliberation prompt for a board seat.

    Pure function naming the seat's title and mandate, the owner's question,
    and asking for a concise answer with a one-line position first.
    """
    title = seat.title or seat.name
    return (
        f"You are {title}.\n"
        f"Mandate: {seat.mandate}\n\n"
        f"Question from the owner:\n{prompt}\n\n"
        "Give a concise answer from your perspective. "
        "The FIRST line of your answer must be your one-line position (e.g. 'Position: ...').\n"
        "This is a read-only deliberation turn: do not use tools, edit files, or execute actions."
    )


def seat_cost_usd(seat: SeatSpec, prompt_text: str, reply_text: str) -> float:
    """Calculate estimated USD spend for a seat turn based on input and output text token estimates."""
    prompt_tokens = max(1, len(prompt_text) // 4)
    output_tokens = max(1, len(reply_text) // 4)
    price = lookup_seat_price(seat.provider, seat.model)
    return price.cost(prompt_tokens, output_tokens)


async def run_seat(
    seat: SeatSpec,
    prompt: str,
    thread_id: str,
    *,
    turn_runner: Callable[[PanelSpeaker, str, str, str | None], Awaitable[TurnOutcome]] = default_turn_runner,
) -> SeatReply:
    """Execute one board seat turn using a real model turn runner."""
    speaker = PanelSpeaker(
        name=seat.title or seat.name,
        perspective=seat.mandate,
        provider=seat.provider,
        # Seat model ids are pricing labels, not CLI ids: run on the provider default (#1637).
        model=None,
    )
    formatted_prompt = seat_prompt(seat, prompt)
    outcome = await turn_runner(speaker, formatted_prompt, thread_id, None)
    if outcome.ok:
        cost = seat_cost_usd(seat, formatted_prompt, outcome.text)
        return SeatReply(
            seat_name=seat.name,
            status="ok",
            text=outcome.text,
            cost_usd=cost,
        )
    return SeatReply(
        seat_name=seat.name,
        status="error",
        text=f"(No response - error: {outcome.error})",
        error_detail=outcome.error or "",
    )
