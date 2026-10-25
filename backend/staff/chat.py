"""Read-only staff chat turns (SC-B4, #1307) with Barb pool reservation (SC-C6).

Uses provider sessions or bounded history replay in isolated scratch directories,
streaming replies through ThreadEventBus with latency metrics and reply contracts.
Adds scoped shared-memory reference context when configured. Failures include
classified diagnoses, remediation hints and retry support.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from typing import TYPE_CHECKING, Any

from staff.adapters import (
    ADAPTERS,
    ChatReadOnlyUnsupportedError,
    ProviderAdapter,
    get_adapter,
)
from staff.availability import (
    execute_degraded_turn,
    get_availability_metrics,
    is_provider_healthy,
    record_successful_turn,
    resolve_provider_chain,
)
from staff.chat_context_budget import cap_chat_context
from staff.chat_failures import (
    chat_read_only_tools,
    choose_preferred_chat_failure,
    record_chat_capacity_failure,
    record_chat_failure_if_pending,
)
from staff.chat_fleet_context import build_fleet_context_block
from staff.chat_handoff import post_reply_handoff as post_reply_handoff
from staff.chat_handoff import run_chat_turn_in_background
from staff.chat_history import (
    DEFAULT_TOKEN_BUDGET,
    build_pending_proposals_block,
    extract_session_id,
    format_history_replay,
)
from staff.chat_issue_context import build_issue_context_block
from staff.chat_knowledge import build_knowledge_turn_block as build_knowledge_turn_block
from staff.chat_memory_summary import extract_summary, finish_summary, summary_instruction
from staff.chat_pool import (
    DEFAULT_BARB_RESERVED_SLOTS,
    DEFAULT_CHAT_ACQUIRE_TIMEOUT,
    DEFAULT_MAX_CHAT_TURNS,
    ChatConcurrencyPool,
    get_chat_pool,
)
from staff.chat_result import ChatTurnResult
from staff.chat_scratch import thread_scratch_dir
from staff.chat_shared_memory import build_shared_memory_context_block
from staff.chat_streaming import (
    LiveProcessReader,
    spawn_cli_process,
    stream_turn_output,
)
from staff.classifier import classify_run_failure
from staff.cli_version import version_gate
from staff.conversations import ConversationStore, get_conversation_store
from staff.owner_requests import request_approves, run_for_requester
from staff.proposal_cards import post_proposal
from staff.reply_contract import ProposedAction, parse_reply
from staff.roles import RoleSpec, load_roles
from staff.standards_registry import load_fleet_standards
from staff.thread_bus import get_thread_bus
from staff.turn_prompt import compose_turn_prompt, context_section

if TYPE_CHECKING:
    from identity import Principal

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

from staff.chat_turn_runner import ChatTurnRunner
