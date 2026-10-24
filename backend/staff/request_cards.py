"""Project stored work-request proposals into the existing approval card contract."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from staff.proposal_cards import CARD_KIND, proposal_card
from staff.thread_bus import get_thread_bus

if TYPE_CHECKING:
    from staff.conversation_models import MessageRecord
    from staff.conversations import ConversationStore


def request_message_card(store: ConversationStore, message: MessageRecord) -> dict[str, Any]:
    """Preserve authorship; only a proposal bound in the store can supply a card.

    Legacy request messages are projected without modifying persisted history. Card
    state comes from the proposal, including after approval, expiration or execution.
    """
    rendered = message.to_dict()
    if not isinstance(message.meta.get("request"), dict):
        return rendered
    proposals = store.list_proposals(thread_id=message.thread_id, message_id=message.id, limit=2)
    if len(proposals) != 1:
        return rendered
    prop = proposals[0]
    rendered["kind"] = CARD_KIND
    rendered["meta"] = {
        **message.meta,
        "proposal": proposal_card(prop, description=message.body_md, proposed_by=message.author),
    }
    return rendered


def publish_request_card(store: ConversationStore, message_id: str) -> None:
    """Persist and publish the current card for a newly recorded work request."""
    message = store.get_message(message_id)
    if message is None:
        return
    rendered = request_message_card(store, message)
    if rendered["kind"] != CARD_KIND:
        return
    saved = store.update_message(message_id, kind=CARD_KIND, meta=rendered["meta"])
    if saved is not None:
        get_thread_bus().publish_message_sync(saved.thread_id, saved.to_dict())
