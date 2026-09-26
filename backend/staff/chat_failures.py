"""Chat-turn failure recording and read-only tool lookup (#1484).

Split out of ``chat.py`` so the turn runner stays under the 500-line cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from staff.thread_bus import get_thread_bus

if TYPE_CHECKING:
    from staff.chat import ChatTurnResult
    from staff.conversations import ConversationStore
    from staff.roles import RoleSpec


FAILURE_SPECIFICITY: dict[str, int] = {
    "auth_expired": 100,
    "provider_not_read_only": 100,
    "invalid_chat_tools": 100,
    "rate_limited": 90,
    "needs_input": 90,
    "lease_blocked": 90,
    "chat_capacity": 90,
    "workspace_error": 80,
    "timeout": 70,
    "stalled": 70,
    "unkillable": 70,
    "orphaned": 70,
    "unknown": 50,
    "provider_error": 30,
    "cli_missing": 20,
}


def failure_specificity(failure_class: str | None) -> int:
    """Return specificity score for a failure class (higher = more specific/actionable)."""
    if not failure_class:
        return 0
    return FAILURE_SPECIFICITY.get(failure_class, 40)


def choose_preferred_chat_failure(
    existing: tuple[str, ChatTurnResult] | None,
    candidate: str,
    candidate_result: ChatTurnResult,
) -> tuple[str, ChatTurnResult]:
    """Select the most specific classified failure between candidates (#1551).

    If the candidate's failure has strictly higher specificity than the existing
    best, it wins. Otherwise, the existing best is preserved because earlier
    candidates in the provider chain represent the primary / preferred configuration.
    """
    if existing is None:
        return (candidate, candidate_result)
    _, existing_result = existing
    existing_score = failure_specificity(existing_result.failure_class)
    new_score = failure_specificity(candidate_result.failure_class)
    if new_score > existing_score:
        return (candidate, candidate_result)
    return existing


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
            "remediation": detail,
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
