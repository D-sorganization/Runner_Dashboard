"""Chat-turn failure recording and read-only tool lookup (#1484).

Split out of ``chat.py`` so the turn runner stays under the 500-line cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from staff.thread_bus import get_thread_bus

if TYPE_CHECKING:
    from staff.conversations import ConversationStore
    from staff.roles import RoleSpec


async def record_chat_failure(
    conv_store: ConversationStore,
    thread_id: str,
    placeholder_id: str,
    *,
    actor: str,
    failure_class: str,
    retryable: bool,
    detail: str,
    error: str | None,
) -> None:
    """Turn a pending reply into a classified error message and publish it.

    Pre: ``failure_class`` is non-empty. Post: the message is ``kind=error``,
    ``delivery=failed`` and carries ``failure_class`` plus a Retry action.
    """
    assert failure_class, "failure_class must be non-empty"  # noqa: S101
    conv_store.update_message(
        placeholder_id,
        kind="error",
        delivery="failed",
        body_md=f"Error from {actor}: {detail}",
        meta={
            "failure_class": failure_class,
            "retryable": retryable,
            "actions": [{"name": "retry", "label": "Retry"}],
            "error": error,
        },
    )
    message = conv_store.get_message(placeholder_id)
    if message:
        await get_thread_bus().publish_message(thread_id, message.to_dict())


def chat_read_only_tools(role: RoleSpec | None) -> tuple[str, ...]:
    """The role's ``chat.read_only_tools`` (empty when unset)."""
    chat = role.chat if role else {}
    tools = chat.get("read_only_tools") if isinstance(chat, dict) else None
    return tuple(tools) if isinstance(tools, list) else ()


async def record_chat_capacity_failure(
    conv_store: ConversationStore,
    thread_id: str,
    placeholder_id: str,
    *,
    user_message_id: str,
    role_name: str,
    detail: str = "All chat slots are busy; please retry shortly.",
) -> None:
    """Mark placeholder reply failed due to capacity saturation and post system message."""
    await record_chat_failure(
        conv_store,
        thread_id,
        placeholder_id,
        actor=role_name,
        failure_class="chat_capacity",
        retryable=True,
        detail=detail,
        error="chat_capacity",
    )
    sys_msg = conv_store.add_message(
        thread_id=thread_id,
        author_kind="system",
        author="system",
        kind="text",
        body_md="All chat slots are busy, please retry.",
        meta={
            "in_reply_to": user_message_id,
            "failure_class": "chat_capacity",
            "retryable": True,
        },
        delivery="complete",
    )
    await get_thread_bus().publish_message(thread_id, sys_msg.to_dict())


async def record_chat_failure_if_pending(
    conv_store: ConversationStore,
    thread_id: str,
    placeholder_id: str,
    *,
    actor: str,
    failure_class: str | None,
    retryable: bool,
    detail: str,
    error: str | None,
) -> None:
    """Record the chain's last failure when no attempt recorded one (#1341).

    Only the chain's last entry records its own failure, so a skipped last
    entry left the reply pending forever. Post: the placeholder is not pending.
    """
    placeholder = conv_store.get_message(placeholder_id)
    if placeholder is None or placeholder.delivery != "pending":
        return
    await record_chat_failure(
        conv_store,
        thread_id,
        placeholder_id,
        actor=actor,
        failure_class=failure_class or "unknown",
        retryable=retryable,
        detail=detail or "Failed to complete reply",
        error=error,
    )
