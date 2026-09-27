"""Confident "Ask Barb (auto-route)" messages go straight to the specialist (#1567).

Without this, every auto-route turn runs as Barb, who answers and may hand off one
turn later (#1548). When the deterministic pre-router is confident, the message is
handed to the chosen role before any turn runs: ``BarbRouter.execute_handoff`` posts
the usual handoff card in the auto thread (reason ``auto-routed: matched <rule>``)
and seeds the role's direct thread with the caller, and the turn then runs there as
that role. The turn runs in the role's own thread rather than the auto thread
because provider sessions are stored per thread: a specialist turn in the auto
thread would resume Barb's session, and Barb's next turn would resume the
specialist's. Ambiguous messages, and any failure here, leave the turn with Barb.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from staff.conversation_models import MessageRecord, ThreadRecord
from staff.conversations import ConversationStore
from staff.roles import load_roles
from staff.router import BarbRouter, route_deterministic
from staff.router_models import RoutingDecision

log = logging.getLogger("dashboard.staff.chat_preroute")

__all__ = [
    "AUTO_ROUTE_ROLE",
    "PRE_ROUTE_CONFIDENCE_THRESHOLD",
    "PRE_ROUTE_MODE",
    "PreRoutedTurn",
    "confident_route",
    "preroute_auto_message",
    "thread_reply_role",
]

AUTO_ROUTE_ROLE = "barb"
PRE_ROUTE_MODE = "auto_route"

PRE_ROUTE_CONFIDENCE_THRESHOLD = 0.85
"""The lowest ``route_deterministic`` confidence that skips Barb's turn.

Contract: a message is pre-routed only when its decision reaches this value. The
pre-router scores 1.0 for ``/role x`` and ``@x``, 0.85 when one role's keywords
match strictly more often than any other role's, and 0.70 when two roles tie.
So an explicit target or a clear keyword winner is confident, and a tie is not.
Barb's own topics score 0.95 but name Barb, so they are never pre-routed.
"""


@dataclass(frozen=True)
class PreRoutedTurn:
    """Where a pre-routed turn runs: *role* replies in its direct thread *thread_id*."""

    role: str
    thread_id: str
    handoff_message_id: str


def thread_reply_role(thread: ThreadRecord, callers: Collection[str]) -> str:
    """The thread's first participant that is not one of *callers*; Barb when there is none."""
    return next((p for p in thread.participants if p and p not in callers), AUTO_ROUTE_ROLE)


def confident_route(text: str, known_roles: Collection[str]) -> RoutingDecision | None:
    """The deterministic decision for *text* when it may skip Barb, else ``None``.

    Pre: ``known_roles`` holds the loaded role names.
    Post: a returned decision names a loaded role other than Barb, carries the rule
    that matched, and has ``confidence >= PRE_ROUTE_CONFIDENCE_THRESHOLD``.
    """
    decision = route_deterministic(text)
    if decision is None or decision.confidence < PRE_ROUTE_CONFIDENCE_THRESHOLD:
        return None
    role = decision.chosen_role
    if not role or role == AUTO_ROUTE_ROLE or role not in known_roles or not decision.matched_rule:
        return None
    return decision


async def preroute_auto_message(
    *,
    thread: ThreadRecord,
    user_msg: MessageRecord,
    caller_id: str,
    store: ConversationStore,
    bus: Any,
    can_chat: Callable[[str], tuple[bool, str]] | None = None,
    known_roles: Collection[str] | None = None,
) -> PreRoutedTurn | None:
    """Hand a confident auto-route message to its specialist; ``None`` leaves it with Barb.

    Pre: *user_msg* is the caller's message, already stored in *thread*.
    Post: ``None`` for a thread that is not ``auto``, a message that is not confident,
    a role that cannot chat (*can_chat*), or any error. Otherwise the auto thread holds
    one new handoff card, published on *bus*. This function never raises.
    """
    if thread.kind != "auto":
        return None
    try:
        return await _preroute(thread, user_msg, caller_id, store, bus, can_chat, known_roles)
    except Exception as exc:  # noqa: BLE001 - any failure must leave the message with Barb
        log.warning("Auto-route pre-routing failed for thread %s; Barb takes the turn: %s", thread.id, exc)
        return None


async def _preroute(
    thread: ThreadRecord,
    user_msg: MessageRecord,
    caller_id: str,
    store: ConversationStore,
    bus: Any,
    can_chat: Callable[[str], tuple[bool, str]] | None,
    known_roles: Collection[str] | None,
) -> PreRoutedTurn | None:
    roles = known_roles if known_roles is not None else load_roles().keys()
    decision = confident_route(user_msg.body_md, roles)
    if decision is None or not decision.chosen_role:
        return None
    if can_chat is not None and not can_chat(decision.chosen_role)[0]:
        return None
    routed = dataclasses.replace(decision, reason=f"auto-routed: matched {decision.matched_rule}", mode=PRE_ROUTE_MODE)
    result = BarbRouter().execute_handoff(
        routed, user_msg.body_md, caller_id, source_thread_id=thread.id, store=store, from_role=AUTO_ROUTE_ROLE
    )
    card = store.get_message(result.handoff_message_id)
    if card:
        try:
            await bus.publish_message(thread.id, card.to_dict())
        except Exception as exc:  # noqa: BLE001 - the card is stored; the stream catches up on replay
            log.warning("Failed to publish auto-route handoff card: %s", exc)
    return PreRoutedTurn(result.target_role, result.target_thread_id, result.handoff_message_id)
