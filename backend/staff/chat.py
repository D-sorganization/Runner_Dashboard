"""Chat turn execution engine for staff conversational replies (SC-B4, Issue #1307).

Provides fast, read-only conversational replies with:
- Dedicated concurrency pool separating chat turns from work runs, with Barb reservation (SC-C6).
- Per-provider session resume with fallback to history replay within a token budget.
- Execution in an isolated scratch directory without worktrees, git push, or RM leases.
- Real-time token streaming to ThreadEventBus for low-latency SSE delivery.
- Turn latency metrics: time to first token (TTFT) and total turn duration.
- Reply contract parsing with action proposal extraction and persistence.
- Classified failure diagnosis with user remediation hints and retry support.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

from staff.actions import registered_risk
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
from staff.chat_failures import (
    chat_read_only_tools,
    record_chat_capacity_failure,
    record_chat_failure,
)
from staff.chat_history import (
    DEFAULT_TOKEN_BUDGET,
    extract_session_id,
    format_history_replay,
)
from staff.chat_knowledge import build_knowledge_turn_block
from staff.chat_pool import (
    DEFAULT_BARB_RESERVED_SLOTS,
    DEFAULT_CHAT_ACQUIRE_TIMEOUT,
    DEFAULT_MAX_CHAT_TURNS,
    ChatConcurrencyPool,
    get_chat_pool,
)
from staff.chat_streaming import (
    LiveProcessReader,
    spawn_cli_process,
    stream_turn_output,
)
from staff.classifier import classify_run_failure
from staff.conversations import ConversationStore, get_conversation_store
from staff.reply_contract import ProposedAction, parse_reply
from staff.roles import RoleSpec, load_roles
from staff.thread_bus import get_thread_bus

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


@dataclass
class ChatTurnResult:
    """Outcome of a conversational chat turn."""

    ok: bool
    reply: str = ""
    session_id: str | None = None
    handoff: str | None = None
    question: str | None = None
    actions: list[ProposedAction] = field(default_factory=list)
    failure_class: str | None = None
    retryable: bool = False
    remediation: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    replayed_history: bool = False


class ChatTurnRunner:
    """Executes conversational chat turns with session resume and token streaming."""

    def __init__(
        self,
        conv_store: ConversationStore | None = None,
        adapters: dict[str, ProviderAdapter] | None = None,
        pool: ChatConcurrencyPool | None = None,
        acquire_timeout: float | None = None,
    ) -> None:
        self.conv_store = conv_store or get_conversation_store()
        self.adapters = adapters if adapters is not None else ADAPTERS
        self.pool = pool or get_chat_pool()
        self.acquire_timeout = acquire_timeout if acquire_timeout is not None else DEFAULT_CHAT_ACQUIRE_TIMEOUT

    def _spawn_cli_process(
        self,
        cmd: list[str],
        cwd: str,
        env: dict[str, str],
    ) -> subprocess.Popen[str]:
        """Spawn the provider CLI subprocess in read-only scratch mode."""
        return spawn_cli_process(cmd=cmd, cwd=cwd, env=env)

    async def execute_turn(
        self,
        thread_id: str,
        user_message_id: str,
        placeholder_id: str,
        role_name: str,
        provider: str | None = None,
    ) -> ChatTurnResult:
        """Execute a conversational chat turn with fallback chain and degraded mode."""
        roles = load_roles()
        role = roles.get(role_name)

        thread = self.conv_store.get_thread(thread_id)
        if not thread:
            return ChatTurnResult(
                ok=False,
                failure_class="thread_not_found",
                remediation="Thread not found.",
            )

        user_msg = self.conv_store.get_message(user_message_id)
        raw_prompt = user_msg.body_md if user_msg else ""
        knowledge_block = build_knowledge_turn_block(role, raw_prompt)
        prompt_text = f"{knowledge_block}\n\n{raw_prompt}" if knowledge_block else raw_prompt

        acquired = await self.pool.acquire(role_name, timeout=self.acquire_timeout)
        if not acquired:
            log.warning(
                "Chat concurrency limit reached for role %s; rejecting turn with chat_capacity",
                role_name,
            )
            await record_chat_capacity_failure(
                self.conv_store,
                thread_id,
                placeholder_id,
                user_message_id=user_message_id,
                role_name=role_name,
            )
            return ChatTurnResult(
                ok=False,
                failure_class="chat_capacity",
                retryable=True,
                remediation="All chat slots are busy; please retry shortly.",
            )

        metrics = get_availability_metrics()
        metrics.record_turn()
        chain = [provider] if provider else resolve_provider_chain(role_name, role=role)

        try:
            fallback_steps = 0
            last_failed: ChatTurnResult | None = None

            for candidate in chain:
                if not is_provider_healthy(candidate):
                    log.info("Provider %s is unhealthy/disabled; skipping", candidate)
                    fallback_steps += 1
                    metrics.record_fallback()
                    continue

                adapter = self.adapters.get(candidate) or get_adapter(candidate)
                existing_session = thread.meta.get("provider_sessions", {}).get(candidate)

                if existing_session:
                    result = await self._run_turn_attempt(
                        thread_id=thread_id,
                        placeholder_id=placeholder_id,
                        role=role,
                        adapter=adapter,
                        prompt=prompt_text,
                        session_id=existing_session,
                        is_resume=True,
                    )
                    if result.ok:
                        record_successful_turn(
                            self.conv_store,
                            thread_id,
                            placeholder_id,
                            candidate,
                            fallback_steps,
                            result,
                        )
                        return result
                    log.info(
                        "Session resume failed for %s on thread %s; falling back to replay",
                        candidate,
                        thread_id,
                    )

                was_fallback = bool(existing_session)
                replay_prompt = format_history_replay(
                    conv_store=self.conv_store,
                    thread_id=thread_id,
                    current_prompt=prompt_text,
                    role=role,
                )
                result = await self._run_turn_attempt(
                    thread_id=thread_id,
                    placeholder_id=placeholder_id,
                    role=role,
                    adapter=adapter,
                    prompt=replay_prompt,
                    session_id=None,
                    is_resume=False,
                    replayed_history=was_fallback,
                    update_on_failure=(candidate == chain[-1]),
                )
                if result.ok:
                    record_successful_turn(
                        self.conv_store,
                        thread_id,
                        placeholder_id,
                        candidate,
                        fallback_steps,
                        result,
                    )
                    return result

                last_failed = result
                log.warning(
                    "Turn attempt failed on %s: %s; falling back",
                    candidate,
                    result.failure_class,
                )
                fallback_steps += 1
                metrics.record_fallback()

            if last_failed is not None:
                return last_failed

            log.warning(
                "All providers unavailable for thread %s; triggering degraded mode",
                thread_id,
            )
            return await execute_degraded_turn(
                thread_id=thread_id,
                user_message_id=user_message_id,
                placeholder_id=placeholder_id,
                role_name=role_name,
                conv_store=self.conv_store,
            )
        finally:
            if acquired:
                self.pool.release(role_name)

    async def _run_turn_attempt(
        self,
        thread_id: str,
        placeholder_id: str,
        role: RoleSpec | None,
        adapter: ProviderAdapter,
        prompt: str,
        session_id: str | None,
        is_resume: bool,
        replayed_history: bool = False,
        update_on_failure: bool = True,
    ) -> ChatTurnResult:
        scratch_dir = tempfile.mkdtemp(prefix="staff_chat_")
        bus = get_thread_bus()

        t_start = time.monotonic()
        t_first_token: float | None = None

        captured_session_id = session_id
        stdout_text: list[str] = []
        stderr_text: list[str] = []

        try:
            try:
                cmd = adapter.chat_argv(
                    prompt=prompt,
                    workdir=scratch_dir,
                    model=role.model if role else None,
                    session_id=session_id,
                    read_only_tools=chat_read_only_tools(role),
                )
            except (ChatReadOnlyUnsupportedError, ValueError) as exc:
                # Fail closed and visibly: never fall back to a writable argv (#1484).
                failure_class = (
                    "provider_not_read_only" if isinstance(exc, ChatReadOnlyUnsupportedError) else "invalid_chat_tools"
                )
                remediation = "Chat with a provider that has a read-only mode, or fix the role's chat.read_only_tools."
                if update_on_failure:
                    await record_chat_failure(
                        self.conv_store,
                        thread_id,
                        placeholder_id,
                        actor=role.name if role else adapter.provider_id,
                        failure_class=failure_class,
                        retryable=False,
                        detail=remediation,
                        error=str(exc),
                    )
                return ChatTurnResult(
                    ok=False,
                    failure_class=failure_class,
                    retryable=False,
                    remediation=remediation,
                )
            env = {**os.environ, **adapter.runtime_env()}

            proc = self._spawn_cli_process(cmd=cmd, cwd=scratch_dir, env=env)

            # Stream stdout lines incrementally and publish tokens live (SC-B1-G10)
            stream_out = await stream_turn_output(
                reader=LiveProcessReader(proc),
                adapter=adapter,
                bus=bus,
                thread_id=thread_id,
                placeholder_id=placeholder_id,
            )
            stdout_text = stream_out.stdout_lines
            stderr_text = stream_out.stderr_lines
            returncode = stream_out.returncode
            if stream_out.session_id:
                captured_session_id = stream_out.session_id
            t_first_token = stream_out.t_first_token
            deltas = stream_out.deltas

            t_end = time.monotonic()
            ttft = (t_first_token - t_start) if t_first_token is not None else (t_end - t_start)
            turn_duration = t_end - t_start

            raw_combined = "".join(stdout_text)

            if returncode != 0:
                classified = classify_run_failure(
                    provider=adapter.provider_id,
                    exit_code=returncode,
                    output_text=raw_combined,
                    error_message="".join(stderr_text),
                )
                if not is_resume and update_on_failure:
                    await record_chat_failure(
                        self.conv_store,
                        thread_id,
                        placeholder_id,
                        actor=role.name if role else adapter.provider_id,
                        failure_class=classified.failure_class,
                        retryable=classified.retryable,
                        detail=classified.remediation or classified.error or "Failed to complete reply",
                        error=classified.error,
                    )

                return ChatTurnResult(
                    ok=False,
                    failure_class=classified.failure_class,
                    retryable=classified.retryable,
                    remediation=classified.remediation,
                )

            # Parse structured reply contract
            full_reply_text = "".join(deltas) if deltas else raw_combined
            parsed = parse_reply(full_reply_text, role=role)

            metrics = {
                "time_to_first_token_seconds": round(ttft, 3),
                "turn_duration_seconds": round(turn_duration, 3),
                "provider": adapter.provider_id,
            }

            # Update placeholder message to complete
            msg_meta: dict[str, Any] = {
                "metrics": metrics,
                "session_id": captured_session_id,
                "warnings": parsed.warnings,
            }
            if replayed_history:
                msg_meta["replayed_history"] = True
            if parsed.handoff:
                msg_meta["handoff"] = parsed.handoff
            if parsed.question:
                msg_meta["question"] = parsed.question

            self.conv_store.update_message(
                placeholder_id,
                kind="text",
                delivery="complete",
                body_md=parsed.reply,
                meta=msg_meta,
            )

            # Create any proposed action proposals in store
            created_proposals: list[ProposedAction] = []
            for action in parsed.actions:
                try:
                    self.conv_store.create_proposal(
                        message_id=placeholder_id,
                        thread_id=thread_id,
                        action=action.action,
                        params=action.params,
                        risk=registered_risk(action.action),
                        principal=role.name if role else "assistant",
                    )
                    created_proposals.append(action)
                    await bus.publish_proposal(
                        thread_id,
                        {
                            "action": action.action,
                            "params": action.params,
                            "reason": action.reason,
                            "message_id": placeholder_id,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("Failed to persist action proposal %s: %s", action.action, exc)

            # Persist provider session id on thread metadata
            if captured_session_id:
                th = self.conv_store.get_thread(thread_id)
                if th:
                    cur_sessions = dict(th.meta.get("provider_sessions", {}))
                    cur_sessions[adapter.provider_id] = captured_session_id
                    th_meta = dict(th.meta)
                    th_meta["provider_sessions"] = cur_sessions
                    self.conv_store.update_thread(thread_id, meta=th_meta)

            # Publish completed message on bus
            completed_msg = self.conv_store.get_message(placeholder_id)
            if completed_msg:
                await bus.publish_message(thread_id, completed_msg.to_dict())

            return ChatTurnResult(
                ok=True,
                reply=parsed.reply,
                session_id=captured_session_id,
                handoff=parsed.handoff,
                question=parsed.question,
                actions=created_proposals,
                metrics=metrics,
            )
        finally:
            shutil.rmtree(scratch_dir, ignore_errors=True)


async def run_chat_turn_in_background(
    thread_id: str,
    user_message_id: str,
    placeholder_id: str,
    role_name: str,
    caller_id: str,
) -> None:
    """Async background task invoked by POST /threads/{id}/messages to execute reply."""
    try:
        runner = ChatTurnRunner()
        await runner.execute_turn(
            thread_id=thread_id,
            user_message_id=user_message_id,
            placeholder_id=placeholder_id,
            role_name=role_name,
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "Unhandled error during chat turn for thread %s: %s",
            thread_id,
            exc,
            exc_info=True,
        )
