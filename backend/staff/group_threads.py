"""Staff group thread lifecycle helpers.

Split from ``staff.groups`` (thread helpers, background turn runner, thread
creation and the ``board.convene`` convening path) to keep
``staff.groups`` under the repo's 500-line soft cap. Owns the "what happens
around a group turn" seam: ``staff.groups`` keeps the turn engine
(``execute_group_turn``, ``collate_consensus``, cost estimation) and the
group registry; this module persists threads/messages and drives the
background fan-out.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from staff.action_executors import BOARD_PROPOSAL_ROLE
from staff.audit import record_audit
from staff.conversation_models import ThreadRecord
from staff.conversations import get_conversation_store
from staff.groups import (
    estimate_group_turn_cost,
    execute_group_turn,
    get_board_group,
    get_group,
)
from staff.proposal_cards import post_proposal
from staff.thread_bus import get_thread_bus

if TYPE_CHECKING:
    from staff.conversations import ConversationStore
    from staff.group_models import GroupDefinition

log = logging.getLogger("dashboard.staff.group_threads")


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
    seat_context = str((user_msg.meta or {}).get("seat_context") or "") if user_msg else ""

    try:
        res = await execute_group_turn(group_id=group_id, prompt=prompt, thread_id=thread_id, seat_context=seat_context)

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
                await post_proposal(
                    store,
                    bus,
                    thread_id=thread_id,
                    author=res.coordinator,
                    action=action.action,
                    params=action.params,
                    reason=action.reason,
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
    if kind != "group" and role not in ("board", BOARD_PROPOSAL_ROLE):
        return kind, role, {}

    group_key = "board" if role in ("board", BOARD_PROPOSAL_ROLE, None) else str(role)
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


def create_group_thread(
    group: GroupDefinition,
    title: str | None,
    caller_id: str,
    store: ConversationStore | None = None,
) -> ThreadRecord:
    """Create a group thread populated with the group's coordinator and seats.

    Shared by ``POST /groups/{id}/threads`` and the ``board.convene`` action so both
    build the same thread shape (DRY; Runner_Dashboard#1762).

    Pre: group is a loaded GroupDefinition.
    Post: returns a persisted ThreadRecord with kind='group', participants including
    every seat and the coordinator (plus caller_id when non-empty and not already a
    participant), and meta {group, coordinator, seats}.
    """
    participants = [group.coordinator] + [s.name for s in group.seats]
    if caller_id and caller_id not in participants:
        participants.append(caller_id)

    conv_store = store or get_conversation_store()
    return conv_store.create_thread(
        title=title or f"{group.name} Deliberation",
        kind="group",
        participants=participants,
        created_by=caller_id,
        meta={
            "group": group.id,
            "coordinator": group.coordinator,
            "seats": [s.name for s in group.seats],
        },
    )


async def convene_board_thread(
    question: str, title: str | None, caller_id: str, include_queue: bool = False
) -> dict[str, Any]:
    """Create a Board group thread and post ``question`` as its first group turn.

    Pre: question is non-empty (the ``board.convene`` executor validates this first).
    With ``include_queue`` the open proposal queue is read first and stored as the turn's
    ``seat_context``, so the seats see every proposal while the question stays short
    (#1787); a failed read raises :class:`~staff.board_queue.QueueUnavailableError`
    before any thread is created.
    Post: returns {"thread_id", "title"}. The user turn and its reply placeholder are
    persisted with cost already confirmed — the caller's approval of the ``board.convene``
    action stands as the cost confirmation (Runner_Dashboard#1762) — and a background task
    fans the question out to every seat exactly as :func:`dispatch_group_message` does.
    """
    meta: dict[str, Any] = {"confirm_cost": True}
    if include_queue:
        from staff.board_queue import fetch_queue_block  # noqa: PLC0415

        meta["seat_context"] = await fetch_queue_block()

    group = get_board_group()
    store = get_conversation_store()
    thread = create_group_thread(group, title, caller_id, store=store)

    user_msg = store.add_message(
        thread_id=thread.id,
        author_kind="user",
        author=caller_id,
        kind="text",
        body_md=question,
        meta=meta,
        delivery="complete",
    )
    reply_placeholder_rec = store.add_message(
        thread_id=thread.id,
        author_kind="role",
        author=group.coordinator,
        kind="text",
        body_md="",
        meta={"in_reply_to": user_msg.id, "is_group_turn": True, "group": group.id},
        delivery="pending",
    )

    bus = get_thread_bus()
    await bus.publish_message(thread.id, user_msg.to_dict())
    await bus.publish_message(thread.id, reply_placeholder_rec.to_dict())
    asyncio.create_task(
        run_group_turn_in_background(thread.id, user_msg.id, reply_placeholder_rec.id, group.id, caller_id)
    )
    return {"thread_id": thread.id, "title": thread.title}


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

    target_role = str(thread.meta.get("coordinator") or BOARD_PROPOSAL_ROLE)
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
