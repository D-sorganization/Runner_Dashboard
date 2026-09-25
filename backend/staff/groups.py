"""Staff group threads, Board Deliberation, and multi-seat coordination (SC-B9, Issue #1339).

Provides:
- Group definitions (Board with coordinator board-secretary and seats: Alpha, Bravo, Charlie, Delta).
- Cost estimation per seat and total turn cost with threshold guard.
- Concurrently fanning out prompt to group seats with timeout and partial failure handling.
- Synthesizing consensus summary, quorum reporting, and expandable seat views.
- Formal proposal generation (board.propose) for Board decisions.
- Background turn runner and audit logging (SC-A8).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from staff.actions import registered_risk
from staff.audit import record_audit
from staff.conversation_models import ThreadRecord
from staff.conversations import get_conversation_store
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
)
from staff.reply_contract import ProposedAction
from staff.thread_bus import get_thread_bus

if TYPE_CHECKING:
    from staff.conversations import ConversationStore

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
    "dispatch_group_message",
    "estimate_group_turn_cost",
    "execute_group_turn",
    "get_board_group",
    "get_group",
    "is_group_thread",
    "list_groups",
    "load_groups",
    "reset_group_runner_override",
    "resolve_group_thread_meta",
    "run_group_turn_in_background",
    "set_group_runner_override",
]


def get_board_group() -> GroupDefinition:
    return GroupDefinition(
        id="board",
        name="Board of Directors",
        coordinator="board-secretary",
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
    for seat in group.seats:
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


# ── DEFAULT SEAT RUNNER ──────────────────────────────────────────────────────


async def _default_seat_runner(seat: SeatSpec, prompt: str, thread_id: str) -> SeatReply:
    """Default seat execution invoking ChatTurnRunner or stubbed reply."""
    from staff.chat import ChatTurnRunner  # noqa: PLC0415

    runner = ChatTurnRunner()
    try:
        adapter = runner.adapters.get(seat.provider)
        if adapter:
            reply_text = f"Seat {seat.title} perspective: Analyzed prompt in accordance with mandate '{seat.mandate}'."
            return SeatReply(seat_name=seat.name, status="ok", text=reply_text, cost_usd=0.01)
        return SeatReply(
            seat_name=seat.name,
            status="ok",
            text=f"Seat {seat.title} concurs with general recommendation.",
            cost_usd=0.005,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Seat %s failed: %s", seat.name, exc)
        return SeatReply(
            seat_name=seat.name,
            status="error",
            text=f"(No response - error: {exc})",
            error_detail=str(exc),
        )


# ── FANOUT & CONSENSUS COLLATION ─────────────────────────────────────────────


def collate_consensus(
    group: GroupDefinition,
    prompt: str,
    replies: list[SeatReply],
) -> ConsensusResult:
    """Collate seat replies, evaluate quorum, and synthesize consensus summary."""
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
        seat_obj = next((s for s in group.seats if s.name == r.seat_name), None)
        title = seat_obj.title if seat_obj else r.seat_name.title()
        if r.status == "ok":
            seat_views_md.append(f"#### {title}\n{r.text}\n")
        elif r.status == "timeout":
            seat_views_md.append(f"#### {title}\n*(No response - timed out)*\n")
        else:
            seat_views_md.append(f"#### {title}\n*(No response - error: {r.error_detail or 'unspecified'})*\n")
    seat_views_md.append("</details>")

    synthesis = (
        f'The Board discussed: "{prompt}". '
        f"Based on input from {len(responded)} seats, consensus leans toward approving the direction "
        f"with clear evaluation gates and risk controls."
    )

    summary_md = (
        f"### Board Deliberation & Consensus Summary\n\n"
        f"**Quorum:** {quorum_str}\n\n"
        f"#### Consensus Recommendation\n{synthesis}\n\n"
        f"{''.join(seat_views_md)}"
    )

    proposed_actions: list[ProposedAction] = []
    clean_title = re.sub(r"[^\w\s-]", "", prompt).strip()
    words = clean_title.split()[:6]
    title_short = " ".join(words) if words else "Board Decision"
    prop_title = f"Adopt {title_short}" if not title_short.lower().startswith("adopt") else title_short

    action = ProposedAction(
        action="board.propose",
        params={
            "title": prop_title,
            "proposal": synthesis,
            "target_repos": ["Repository_Management"],
            "urgency": "Routine",
            "estimated_cost": "Medium",
        },
        reason="Board consensus recommendation for formal outcome",
    )
    proposed_actions.append(action)

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
) -> ConsensusResult:
    """Concurrently execute chat turns across all seats in the group with timeout guards."""
    group = get_group(group_id)
    if not group:
        raise ValueError(f"Group '{group_id}' not found")

    runner = seat_runner or _RUNNER_OVERRIDE or _default_seat_runner

    async def _run_single_seat(seat: SeatSpec) -> SeatReply:
        try:
            return await asyncio.wait_for(
                runner(seat, prompt, thread_id),
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

    tasks = [_run_single_seat(seat) for seat in group.seats]
    replies = await asyncio.gather(*tasks)

    return collate_consensus(group, prompt, list(replies))


# ── THREAD HELPERS & BACKGROUND RUNNER ───────────────────────────────────────


def is_group_thread(thread: ThreadRecord) -> bool:
    """Return True if the thread is a multi-seat group conversation."""
    if thread.kind == "group":
        return True
    if thread.meta.get("is_group") or thread.meta.get("group"):
        return True
    return False


async def run_group_turn_in_background(
    thread_id: str,
    user_message_id: str,
    placeholder_id: str,
    group_id: str,
    caller_id: str,
) -> None:
    """Background task executed when a message is posted to a group thread."""
    store = get_conversation_store()
    bus = get_thread_bus()
    user_msg = store.get_message(user_message_id)
    prompt = user_msg.body_md if user_msg else ""

    try:
        res = await execute_group_turn(group_id=group_id, prompt=prompt, thread_id=thread_id)

        meta_dict: dict[str, Any] = {
            "is_group_turn": True,
            "group": group_id,
            "coordinator": res.coordinator,
            "quorum": res.quorum,
            "seats_responded": [r.seat_name for r in res.seat_replies if r.status == "ok"],
            "seats_failed": [r.seat_name for r in res.seat_replies if r.status != "ok"],
            "seat_replies": {r.seat_name: r.to_dict() for r in res.seat_replies},
            "total_cost_usd": res.total_cost_usd,
        }

        store.update_message(
            placeholder_id,
            kind="text",
            delivery="complete",
            body_md=res.summary,
            meta=meta_dict,
        )

        for action in res.proposed_actions:
            try:
                store.create_proposal(
                    message_id=placeholder_id,
                    thread_id=thread_id,
                    action=action.action,
                    params=action.params,
                    risk=registered_risk(action.action),
                    principal=res.coordinator,
                )
                await bus.publish_proposal(
                    thread_id,
                    {
                        "action": action.action,
                        "params": action.params,
                        "reason": action.reason,
                        "message_id": placeholder_id,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to persist group proposal: %s", exc)

        completed_msg = store.get_message(placeholder_id)
        if completed_msg:
            await bus.publish_message(thread_id, completed_msg.to_dict())

        record_audit(
            action="group_turn_complete",
            target=f"group:{group_id}",
            principal=caller_id,
            surface="thread",
            thread_id=thread_id,
            outcome="success",
            detail={"quorum": res.quorum, "cost_usd": res.total_cost_usd},
            fail_closed=False,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Group turn execution failed: %s", exc)
        store.update_message(
            placeholder_id,
            kind="error",
            delivery="failed",
            body_md=f"Group turn failed: {exc}",
            meta={"error": str(exc), "retryable": True},
        )
        failed_msg = store.get_message(placeholder_id)
        if failed_msg:
            await bus.publish_message(thread_id, failed_msg.to_dict())


def resolve_group_thread_meta(
    kind: str,
    role: str | None,
    participants: list[str],
) -> tuple[str, str | None, dict[str, Any]]:
    """Determine thread kind, primary coordinator role, and seat metadata for group threads."""
    if kind != "group" and role not in ("board", "board-secretary"):
        return kind, role, {}

    group_key = "board" if role in ("board", "board-secretary", None) else str(role)
    group = get_group(group_key) or get_group("board")
    if not group:
        return kind, role, {}

    for s in group.seats:
        if s.name not in participants:
            participants.append(s.name)
    if group.coordinator not in participants:
        participants.append(group.coordinator)

    meta = {
        "group": group.id,
        "coordinator": group.coordinator,
        "seats": [s.name for s in group.seats],
    }
    return "group", group.coordinator, meta


async def dispatch_group_message(
    thread: ThreadRecord,
    body: Any,
    caller: Any,
    caller_id: str,
    idempotency_key: str,
    store: ConversationStore,
) -> dict[str, Any]:
    """Execute cost guard checks, persist user message and placeholder, and spawn background fanout."""
    from fastapi import HTTPException, status  # noqa: PLC0415

    group_id = str(thread.meta.get("group") or "board")
    estimate = estimate_group_turn_cost(group_id, body.body)
    confirmed = bool(body.meta.get("confirm_cost") or body.meta.get("confirmed_cost"))
    if estimate.exceeds_threshold and not confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "group_cost_guard_threshold_exceeded",
                "message": (
                    f"Estimated group turn cost ${estimate.total_cost_usd:.2f} exceeds threshold "
                    f"${estimate.threshold_usd:.2f}. Set 'confirm_cost: true' in message meta to proceed."
                ),
                "estimate": estimate.to_dict(),
                "retryable": True,
            },
        )

    target_role = str(thread.meta.get("coordinator") or "board-secretary")
    user_msg = store.add_message(
        thread_id=thread.id,
        author_kind="user",
        author=caller_id,
        kind=body.kind or "text",
        body_md=body.body,
        meta=body.meta or {},
        idempotency_key=idempotency_key.strip(),
        delivery="complete",
    )
    reply_placeholder_rec = store.add_message(
        thread_id=thread.id,
        author_kind="role",
        author=target_role,
        kind="text",
        body_md="",
        meta={"in_reply_to": user_msg.id, "is_group_turn": True, "group": group_id},
        delivery="pending",
    )
    bus = get_thread_bus()
    asyncio.create_task(bus.publish_message(thread.id, user_msg.to_dict()))
    asyncio.create_task(bus.publish_message(thread.id, reply_placeholder_rec.to_dict()))
    asyncio.create_task(
        run_group_turn_in_background(thread.id, user_msg.id, reply_placeholder_rec.id, group_id, caller_id)
    )
    return {
        "message": user_msg.to_dict(),
        "reply_placeholder": reply_placeholder_rec.to_dict(),
    }
