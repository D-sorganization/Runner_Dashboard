"""Read-only staff chat turns (SC-B4, #1307) with Barb pool reservation (SC-C6).

Uses provider sessions or bounded history replay in isolated scratch directories,
streaming replies through ThreadEventBus with latency metrics and reply contracts.
Adds scoped shared-memory reference context when configured. Failures include
classified diagnoses, remediation hints and retry support.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from staff.chat_handoff import post_reply_handoff as post_reply_handoff
from staff.chat_handoff import run_chat_turn_in_background
from staff.chat_history import (
    DEFAULT_TOKEN_BUDGET,
    extract_session_id,
    format_history_replay,
)
from staff.chat_knowledge import build_knowledge_turn_block as build_knowledge_turn_block
from staff.chat_pool import (
    DEFAULT_BARB_RESERVED_SLOTS,
    DEFAULT_MAX_CHAT_TURNS,
    ChatConcurrencyPool,
    get_chat_pool,
)
from staff.chat_result import ChatTurnResult

if TYPE_CHECKING:
    pass

log = logging.getLogger("dashboard.staff.chat")

__all__ = [
    "DEFAULT_BARB_RESERVED_SLOTS",
    "DEFAULT_MAX_CHAT_TURNS",
    "DEFAULT_TOKEN_BUDGET",
    "ChatConcurrencyPool",
    "ChatTurnResult",
    "ChatTurnRunner",
    "build_knowledge_turn_block",
    "extract_session_id",
    "format_history_replay",
    "get_chat_pool",
    "run_chat_turn_in_background",
]

from staff.chat_turn_runner import ChatTurnRunner  # noqa: E402
