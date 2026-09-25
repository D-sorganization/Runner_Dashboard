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
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> str:
    """Format conversation history and role persona into a prompt for history replay.

    Estimates ~4 characters per token to ensure prompt stays strictly within budget.
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
    messages = conv_store.list_messages(thread_id, limit=50)

    turns: list[str] = []
    for m in messages:
        if m.kind in ("text", "action_result") and m.body_md.strip():
            role_title = getattr(role, "title", None) or (role.name.title() if role else "Assistant")
            prefix = "User" if m.author_kind == "user" else role_title
            turns.append(f"{prefix}: {m.body_md.strip()}")

    current_turn = f"User: {current_prompt.strip()}\nAssistant:"

    included_turns: list[str] = []
    base_len = len(persona_header) + len(contract_fragment) + len(current_turn)
    remaining_chars = max(0, char_budget - base_len)

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
    if dialogue_section:
        parts.append(f"### Prior Conversation\n{dialogue_section}\n\n")
    parts.append(current_turn)

    return "".join(parts)
