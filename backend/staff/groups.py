"""Staff group threads, Board Deliberation, and multi-seat coordination (SC-B9, Issue #1339).

Provides:
- Group definitions (Board with coordinator board-secretary and seats: Alpha, Bravo, Charlie, Delta).
- Cost estimation per seat and total turn cost with threshold guard.
- Concurrently fanning out prompt to group seats with timeout and partial failure handling.
- Synthesizing consensus summary, quorum reporting, and expandable seat views.
- Formal proposal generation (board.propose) for Board decisions.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from staff.action_executors import BOARD_PROPOSAL_ROLE
from staff.chat_issue_context import BOARD_BLOCK_CHARS, BOARD_MD_FILE_CHARS, build_referenced_items_block
from staff.group_models import (
    BOARD_SEATS,
    DEFAULT_COST_THRESHOLD_USD,
    DEFAULT_SEAT_TIMEOUT_SECONDS,
    ConsensusResult,
    GroupCostEstimate,
    GroupDefinition,
    SeatReply,
    SeatSpec,
    get_group_threshold,
    lookup_seat_price,
    seat_label,
    seat_on_node,
)
from staff.group_seat_runner import run_seat
from staff.reply_contract import ProposedAction

if TYPE_CHECKING:
    from staff.chat_issue_context import IssueFetcher

log = logging.getLogger("dashboard.staff.groups")

__all__ = [
    "BOARD_SEATS",
    "DEFAULT_COST_THRESHOLD_USD",
    "DEFAULT_SEAT_TIMEOUT_SECONDS",
    "ConsensusResult",
    "GroupCostEstimate",
    "GroupDefinition",
    "SeatReply",
    "SeatSpec",
    "collate_consensus",
    "estimate_group_turn_cost",
    "execute_group_turn",
    "get_board_group",
    "get_group",
    "list_groups",
    "load_groups",
    "reset_group_runner_override",
    "set_group_runner_override",
]


def get_board_group() -> GroupDefinition:
    return GroupDefinition(
        id="board",
        name="Board of Directors",
        coordinator=BOARD_PROPOSAL_ROLE,
        seats=BOARD_SEATS,
        description="Collective priority and governance council coordinated by Board Secretary.",
        cost_threshold_usd=get_group_threshold(),
    )


def load_groups() -> dict[str, GroupDefinition]:
    """Load registered groups with RM scope fallback."""
    board = get_board_group()
    return {board.id: board}


def get_group(group_id: str) -> GroupDefinition | None:
    return load_groups().get(group_id.lower())


def list_groups() -> list[GroupDefinition]:
    return list(load_groups().values())


# ── TEST RUNNER OVERRIDES ────────────────────────────────────────────────────

SeatRunnerCallable = Callable[[SeatSpec, str, str], Awaitable[SeatReply]]
_RUNNER_OVERRIDE: SeatRunnerCallable | None = None


def set_group_runner_override(runner: SeatRunnerCallable | None) -> None:
    global _RUNNER_OVERRIDE
    _RUNNER_OVERRIDE = runner


def reset_group_runner_override() -> None:
    global _RUNNER_OVERRIDE
    _RUNNER_OVERRIDE = None


# ── COST ESTIMATION ──────────────────────────────────────────────────────────


def estimate_group_turn_cost(group_id: str, prompt: str = "") -> GroupCostEstimate:
    """Calculate estimated input/output USD spend for all seats in a group."""
    group = get_group(group_id)
    if not group:
        return GroupCostEstimate(
            group_id=group_id,
            total_cost_usd=0.0,
            cost_per_seat={},
            exceeds_threshold=False,
            threshold_usd=get_group_threshold(),
        )

    # Estimate ~4 chars per token; average seat turn outputs ~800 tokens
    prompt_tokens = max(100, int(len(prompt) / 4))
    estimated_output_tokens = 800

    seat_costs: dict[str, float] = {}
    total_cost = 0.0
    for seat in map(seat_on_node, group.seats):  # price what actually runs (#1646)
        price = lookup_seat_price(seat.provider, seat.model)
        seat_cost = price.cost(prompt_tokens, estimated_output_tokens)
        seat_costs[seat.name] = seat_cost
        total_cost += seat_cost

    threshold = group.cost_threshold_usd
    exceeds = total_cost >= threshold
    warning = None
    if exceeds:
        warning = (
            f"Estimated group turn cost ${total_cost:.2f} exceeds threshold "
            f"${threshold:.2f}. Set 'confirm_cost: true' in message meta to proceed."
        )

    return GroupCostEstimate(
        group_id=group.id,
        total_cost_usd=total_cost,
        cost_per_seat=seat_costs,
        exceeds_threshold=exceeds,
        threshold_usd=threshold,
        warning=warning,
    )


# ── FANOUT & CONSENSUS COLLATION ─────────────────────────────────────────────


POSITION_CHARS = 240


def _seat_title(group: GroupDefinition, seat_name: str) -> str:
    seat = next((s for s in group.seats if s.name == seat_name), None)
    return seat_label(seat) if seat else seat_name.title()


def _position(text: str) -> str:
    """A seat's reply on one line, cut to :data:`POSITION_CHARS`."""
    flat = " ".join(text.split())
    return flat if len(flat) <= POSITION_CHARS else flat[: POSITION_CHARS - 1].rstrip() + "…"


def _positions_md(group: GroupDefinition, responded: list[SeatReply]) -> str:
    """Each answering seat's own position. It reports what was said and never claims agreement (#1540)."""
    if not responded:
        return "#### No quorum\nNo seat answered, so the Board has no position on this question."
    lines = [f"- **{_seat_title(group, r.seat_name)}:** {_position(r.text)}" for r in responded]
    return "#### Where the seats stand\n" + "\n".join(lines)


def _board_proposal(prompt: str, positions: str) -> ProposedAction:
    """A `board.propose` built only from the discussion; the approver decides scope and urgency."""
    words = re.sub(r"[^\w\s-]", "", prompt).split()[:6]
    title_short = " ".join(words) or "Board Decision"
    title = title_short if title_short.lower().startswith("adopt") else f"Adopt {title_short}"
    return ProposedAction(
        action="board.propose",
        params={"title": title, "proposal": f"Question: {prompt}\n\n{positions}"},
        reason="Board seats answered; the user decides whether to make it a formal proposal",
    )


def collate_consensus(
    group: GroupDefinition,
    prompt: str,
    replies: list[SeatReply],
) -> ConsensusResult:
    """Collate seat replies, evaluate quorum, and report each answering seat's position."""
    responded = [r for r in replies if r.status == "ok"]
    failed = [r for r in replies if r.status != "ok"]
    total = len(group.seats)

    quorum_parts = [f"{len(responded)}/{total} seats answered"]
    if responded:
        quorum_parts.append(f"({', '.join(r.seat_name.title() for r in responded)}")
    if failed:
        failed_desc = "; ".join(f"{f.seat_name.title()}: no response" for f in failed)
        if responded:
            quorum_parts[-1] += f"; {failed_desc})"
        else:
            quorum_parts.append(f"({failed_desc})")
    elif responded:
        quorum_parts[-1] += ")"

    quorum_str = " ".join(quorum_parts)

    seat_views_md = [f"<details>\n<summary>Seat Replies ({len(responded)}/{total})</summary>\n"]
    for r in replies:
        title = _seat_title(group, r.seat_name)
        if r.status == "ok":
            seat_views_md.append(f"#### {title}\n{r.text}\n")
        elif r.status == "timeout":
            seat_views_md.append(f"#### {title}\n*(No response - timed out)*\n")
        else:
            seat_views_md.append(f"#### {title}\n*(No response - error: {r.error_detail or 'unspecified'})*\n")
    seat_views_md.append("</details>")

    positions = _positions_md(group, responded)
    summary_md = f"### Board Deliberation\n\n**Quorum:** {quorum_str}\n\n{positions}\n\n{''.join(seat_views_md)}"
    proposed_actions = [_board_proposal(prompt, positions)] if responded else []

    total_cost = sum(r.cost_usd for r in replies)
    return ConsensusResult(
        group_id=group.id,
        coordinator=group.coordinator,
        quorum=quorum_str,
        summary=summary_md,
        seat_replies=replies,
        proposed_actions=proposed_actions,
        total_cost_usd=total_cost,
    )


async def execute_group_turn(
    group_id: str,
    prompt: str,
    thread_id: str = "",
    seat_runner: SeatRunnerCallable | None = None,
    seat_timeout_seconds: float = DEFAULT_SEAT_TIMEOUT_SECONDS,
    fetch: IssueFetcher | None = None,
    seat_context: str = "",
) -> ConsensusResult:
    """Concurrently execute chat turns across all seats in the group with timeout guards.

    Before fanning out, resolves any issue/PR references in ``prompt`` (Board budget:
    :data:`~staff.chat_issue_context.BOARD_MD_FILE_CHARS` /
    :data:`~staff.chat_issue_context.BOARD_BLOCK_CHARS`) and, when found, appends the
    fetched '## Referenced items' block to each seat's prompt only — ``prompt`` itself
    stays unchanged for :func:`collate_consensus` and the ``board.propose`` card, so a
    60 KB packet is never pasted into the proposal (Runner_Dashboard#1767). A fetch
    failure is logged and never fails the turn. ``seat_context`` (for example the open
    proposal queue, #1787) is likewise appended to the seats' prompt only, and references
    inside it are resolved too.
    """
    group = get_group(group_id)
    if not group:
        raise ValueError(f"Group '{group_id}' not found")

    runner = seat_runner or _RUNNER_OVERRIDE or run_seat

    seat_prompt_text = f"{prompt}\n\n{seat_context}" if seat_context else prompt
    try:
        block = await build_referenced_items_block(
            seat_prompt_text,
            md_chars=BOARD_MD_FILE_CHARS,
            block_chars=BOARD_BLOCK_CHARS,
            fetch=fetch,
        )
        if block:
            seat_prompt_text = seat_prompt_text + "\n\n" + block
    except Exception as exc:  # noqa: BLE001 — a fetch failure never fails the turn
        log.warning("Referenced-items fetch failed for group turn %r: %s", group_id, exc)

    async def _run_single_seat(seat: SeatSpec) -> SeatReply:
        try:
            return await asyncio.wait_for(
                runner(seat, seat_prompt_text, thread_id),
                timeout=seat_timeout_seconds,
            )
        except TimeoutError:
            log.warning("Seat %s timed out after %.1fs", seat.name, seat_timeout_seconds)
            return SeatReply(
                seat_name=seat.name,
                status="timeout",
                text="(No response - timed out)",
                error_detail=f"Timed out after {seat_timeout_seconds}s",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Seat %s encountered error: %s", seat.name, exc)
            return SeatReply(
                seat_name=seat.name,
                status="error",
                text=f"(No response - error: {exc})",
                error_detail=str(exc),
            )

    tasks = [_run_single_seat(seat_on_node(seat)) for seat in group.seats]
    replies = await asyncio.gather(*tasks)

    return collate_consensus(group, prompt, list(replies))
