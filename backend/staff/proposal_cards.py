"""Proposed actions as ActionCard messages in their thread (#1547).

A proposal is posted as its own ``action_proposal`` message whose ``meta.proposal`` is the
card the Console renders (``ActionProposalData`` in ``StaffConsole/cards/cardTypes.ts``).
The proposal's ``message_id`` is that message, so a decision rewrites the card in place and
the card survives a reload. Every write is published on the thread bus.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol

from staff.actions import registered_risk

if TYPE_CHECKING:
    from staff.conversation_models import ActionProposalRecord
    from staff.conversations import ConversationStore

log = logging.getLogger("dashboard.staff.proposal_cards")

CARD_KIND = "action_proposal"

# Proposal store state -> card status (``ProposalStatus`` in cardTypes.ts).
_STATUS_BY_STATE = {
    "proposed": "pending",
    "approved": "approved",
    "executing": "approved",
    "denied": "denied",
    "done": "executed",
    "failed": "failed",
    "expired": "expired",
}


class MessagePublisher(Protocol):
    async def publish_message(self, thread_id: str, message_dict: dict[str, Any]) -> int: ...


def proposal_card(prop: ActionProposalRecord, *, description: str, proposed_by: str) -> dict[str, Any]:
    """The card for ``prop``. Post: ``status`` is a ``ProposalStatus`` (unknown states read ``pending``)."""
    return {
        "id": prop.id,
        "action_name": prop.action,
        "params": dict(prop.params),
        "risk_level": prop.risk,
        "status": _STATUS_BY_STATE.get(prop.state, "pending"),
        "proposed_by": proposed_by,
        "decided_by": prop.decided_by or None,
        "decided_at": prop.decided_at,
        "description": description,
    }


async def post_proposal(
    store: ConversationStore,
    bus: MessagePublisher,
    *,
    thread_id: str,
    author: str,
    action: str,
    params: dict[str, Any],
    reason: str,
) -> ActionProposalRecord:
    """Record a proposal from role ``author`` and post its card in ``thread_id``.

    Pre: ``thread_id`` exists. Post: the proposal's ``message_id`` is a ``role`` message by
    ``author`` (so role permission checks see the proposer) holding the card; the risk is the
    registry's, never the proposer's.
    """
    msg = store.add_message(thread_id=thread_id, author_kind="role", author=author, kind=CARD_KIND, body_md=reason)
    prop = store.create_proposal(
        message_id=msg.id,
        thread_id=thread_id,
        action=action,
        params=params,
        risk=registered_risk(action),
        principal=author,
    )
    await _write_card(store, bus, msg.id, thread_id, proposal_card(prop, description=reason, proposed_by=author))
    return prop


async def refresh_proposal_card(store: ConversationStore, bus: MessagePublisher, proposal_id: str) -> None:
    """Rewrite the card of ``proposal_id`` from its current state.

    A proposal whose message is not a card (a detection's text message, say) is left alone.
    """
    prop = store.get_proposal(proposal_id)
    msg = store.get_message(prop.message_id) if prop else None
    if prop is None or msg is None or msg.kind != CARD_KIND:
        return
    old = msg.meta.get("proposal") or {}
    card = proposal_card(
        prop,
        description=str(old.get("description") or msg.body_md),
        proposed_by=str(old.get("proposed_by") or msg.author),
    )
    await _write_card(store, bus, msg.id, msg.thread_id, card)


async def _write_card(
    store: ConversationStore, bus: MessagePublisher, message_id: str, thread_id: str, card: dict[str, Any]
) -> None:
    saved = store.update_message(message_id, meta={"proposal": card})
    if saved is not None:
        await bus.publish_message(thread_id, saved.to_dict())
