"""Chat history replay and session extraction utilities (SC-B4, Issue #1307).

Provides:
- Extraction of provider session IDs from CLI stream-json events or plain-text lines.
- History replay prompt formatting with persona injection and strict token budget enforcement.
"""

from __future__ import annotations

import re
from typing import Any

from staff.conversations import ConversationStore
from staff.reply_contract import get_chat_contract_text
from staff.roles import RoleSpec

DEFAULT_TOKEN_BUDGET = 4000

__all__ = [
    "DEFAULT_TOKEN_BUDGET",
    "build_pending_proposals_block",
    "extract_session_id",
    "format_history_replay",
]


def build_pending_proposals_block(conv_store: ConversationStore, thread_id: str) -> str | None:
    """Format pending proposals awaiting owner decision for turn context (#1716)."""
    pending = conv_store.list_proposals(thread_id=thread_id, state="proposed")
    if not pending:
        return None
    items = [f"- {p.id}: {p.action} (params: {p.params})" for p in pending]
    return (
        "Pending action proposals awaiting owner decision in Staff Console:\n"
        + "\n".join(items)
        + "\nProposals cannot be executed directly from chat. Remind the user to approve them in the Staff Console."
    )


_CODEX_SESSION_RE = re.compile(r"Session(?:\s+ID)?:\s*([a-zA-Z0-9_-]+)", re.IGNORECASE)


def extract_session_id(provider: str, event: dict[str, Any], raw_line: str = "") -> str | None:
    """Extract a provider session ID from an event dict or raw line.

    Recognizes:
    - Claude Code init events: ``{"type": "init", "session_id": "..."}``
    - Cursor Agent init events: ``{"chat_id": "..."}`` or ``{"conversation_id": "..."}``
    - Antigravity init/result events: ``{"conversation_id": "..."}``
    - Codex stdout text: ``Session: <session_id>`` or json session_id
    """
    raw = event.get("raw")
    if isinstance(raw, dict):
        targets: list[dict[str, Any]] = [raw]
        for k in ("init", "data", "result", "payload"):
            v = raw.get(k)
            if isinstance(v, dict):
                targets.append(v)
        for t in targets:
            for key in ("session_id", "sessionId", "chat_id", "chatId", "conversation_id", "conversationId"):
                sval = t.get(key)
                if isinstance(sval, str) and sval.strip():
                    return sval.strip()

    text = event.get("text") or raw_line
    if text:
        m = _CODEX_SESSION_RE.search(text)
        if m:
            return m.group(1).strip()

    return None


def format_history_replay(
    conv_store: ConversationStore,
    thread_id: str,
    current_prompt: str,
    role: RoleSpec | None,
    *,
    context: str = "",
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> str:
    """Format conversation history, role persona, and gathered context for replay (#1307, #1649).

    Preconditions:
        conv_store: Active ConversationStore instance.
        thread_id: Conversation thread identifier.
        current_prompt: The raw user message prompt for the current turn.
        role: RoleSpec defining assistant identity and instructions.
        context: Optional dashboard-gathered reference context block to inject.
        token_budget: Token budget for the replayed prior turns (estimates ~4 chars/token).

    Postconditions:
        The prior turns plus the current turn stay within the budget, newest turns kept
        first. The persona and the context are not charged against it: they have their
        own size caps, and charging them let a long persona plus a full fleet block
        squeeze every prior turn out of the replay (#1655).
    """
    char_budget = token_budget * 4

    persona_header = ""
    if role:
        role_title = getattr(role, "title", None) or getattr(role, "name", "Assistant").title()
        role_body = (
            getattr(role, "instructions", "") or getattr(role, "prompt_template", "") or getattr(role, "summary", "")
        )
        persona_header = f"### Role: {role_title}\n{role.summary}\n\n{role_body}\n\n"

    contract_fragment = get_chat_contract_text() + "\n\n"
    context_fragment = f"{context.strip()}\n\n" if context.strip() else ""
    messages = conv_store.list_messages(thread_id, limit=50)

    turns: list[str] = []
    last_collected = None
    for m in messages:
        # Skip system messages such as dashboard routing notices (#1631).
        if m.author_kind == "system":
            continue
        if m.kind in ("text", "action_result") and m.body_md.strip():
            role_title = getattr(role, "title", None) or (role.name.title() if role else "Assistant")
            prefix = "User" if m.author_kind == "user" else role_title
            turns.append(f"{prefix}: {m.body_md.strip()}")
            last_collected = m
        elif m.kind == "action_proposal":
            prop_data = m.meta.get("proposal") or {}
            prop_id = prop_data.get("id") or m.id
            action_name = prop_data.get("action_name", "")
            status = prop_data.get("status", "pending")
            desc = prop_data.get("description") or m.body_md.strip()
            turns.append(f"System: [Action Proposal {prop_id}: {action_name} ({status})] {desc}")
            last_collected = m

    # Drop current user message if already persisted as the last turn (#1631).
    if (
        last_collected
        and last_collected.author_kind == "user"
        and last_collected.body_md.strip() == current_prompt.strip()
    ):
        turns.pop()

    current_turn = f"User: {current_prompt.strip()}\nAssistant:"

    included_turns: list[str] = []
    remaining_chars = max(0, char_budget - len(current_turn))

    for turn in reversed(turns):
        turn_len = len(turn) + 2
        if turn_len <= remaining_chars:
            included_turns.append(turn)
            remaining_chars -= turn_len
        else:
            break

    included_turns.reverse()
    dialogue_section = "\n\n".join(included_turns)

    parts: list[str] = [persona_header, contract_fragment]
    if context_fragment:
        parts.append(context_fragment)
    if dialogue_section:
        parts.append(f"### Prior Conversation\n{dialogue_section}\n\n")
    parts.append(current_turn)

    return "".join(parts)
