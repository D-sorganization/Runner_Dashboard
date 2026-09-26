"""A role reply ending ``handoff: <role>`` becomes a handoff card and moves the work (#1548).

The reply parser only records the target on the reply (``meta.handoff``). This
module turns it into the same handoff Barb's router makes: a ``kind="handoff"``
card in the source thread, and the target role's direct thread with the caller,
seeded with the original request. ``BarbRouter.execute_handoff`` does the work,
so routed and reply handoffs share one path.
"""

from __future__ import annotations

import logging
from collections.abc import Collection

from staff.conversations import ConversationStore, get_conversation_store
from staff.roles import load_roles
from staff.router import BarbRouter
from staff.router_models import HandoffResult, RoutingDecision
from staff.thread_bus import ThreadEventBus, get_thread_bus

log = logging.getLogger("dashboard")

REASON_LIMIT = 280
NO_REASON = "Handed over without a reason."
REPLY_HANDOFF_MODE = "reply"


def handoff_reason(reply: str) -> str:
    """The card's reason: the reply's first paragraph, capped at ``REASON_LIMIT`` characters."""
    first = (reply or "").strip().split("\n\n", 1)[0].strip()
    if not first:
        return NO_REASON
    return first if len(first) <= REASON_LIMIT else first[: REASON_LIMIT - 1].rstrip() + "…"


async def post_reply_handoff(
    *,
    thread_id: str,
    user_message_id: str,
    from_role: str,
    to_role: str,
    reply: str,
    caller_id: str,
    known_roles: Collection[str] | None = None,
    store: ConversationStore | None = None,
    bus: ThreadEventBus | None = None,
) -> HandoffResult | None:
    """Hand the thread's request from *from_role* to *to_role*; ``None`` when the target is not a real role.

    Pre: ``thread_id``, ``from_role`` and ``caller_id`` are non-empty.
    Post: on success the source thread holds one new ``kind="handoff"`` message, published on the bus.
    """
    assert thread_id and from_role and caller_id, "a reply handoff needs a thread, a sender and a caller"
    roles = known_roles if known_roles is not None else load_roles().keys()
    if to_role == from_role or to_role not in roles:
        log.warning("Ignoring reply handoff from %s to %r in thread %s", from_role, to_role, thread_id)
        return None

    s = store or get_conversation_store()
    request = s.get_message(user_message_id)
    decision = RoutingDecision(
        chosen_role=to_role, confidence=1.0, reason=handoff_reason(reply), mode=REPLY_HANDOFF_MODE
    )
    result = BarbRouter().execute_handoff(
        decision,
        request.body_md if request else "",
        caller_id,
        source_thread_id=thread_id,
        store=s,
        from_role=from_role,
    )
    card = s.get_message(result.handoff_message_id)
    if card:
        await (bus or get_thread_bus()).publish_message(thread_id, card.to_dict())
    return result
