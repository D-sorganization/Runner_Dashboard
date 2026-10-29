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
        stdin_text: str | None = None,
    ) -> subprocess.Popen[str]:
        """Spawn the provider CLI subprocess in read-only scratch mode."""
        return spawn_cli_process(cmd=cmd, cwd=cwd, env=env, stdin_text=stdin_text)

    async def execute_turn(
        self,
        thread_id: str,
        user_message_id: str,
        placeholder_id: str,
        role_name: str,
        provider: str | None = None,
        requester: Principal | None = None,
    ) -> ChatTurnResult:
        """Execute a conversational chat turn with fallback chain and degraded mode.

        ``requester`` is who sent the message; when their message may approve an action
        the role proposes, it runs at once instead of waiting on a card (#1786).
        """
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
        digest = str(thread.meta.get("handoff_digest") or "").strip()
        st = load_fleet_standards()
        context_blocks = cap_chat_context(
            (
                f"## Fleet standards Tier 0 ({st.pin})\n\n{st.body}",
                await asyncio.to_thread(build_knowledge_turn_block, role, raw_prompt),
                f"## Handoff digest\n\n{digest[:2400]}" if digest else None,
                await build_fleet_context_block(role, raw_prompt=raw_prompt),
                await build_issue_context_block(role, raw_prompt),
                await build_shared_memory_context_block(role_name, requester),
                build_pending_proposals_block(self.conv_store, thread_id),
                summary_instruction(role_name, requester),
            )
        )
        prompt_text = compose_turn_prompt(context_blocks, raw_prompt)
        turn_epoch = thread.epoch

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
            best_failed: tuple[str, ChatTurnResult] | None = None

            for candidate in chain:
                if fallback_steps:
                    turn_epoch = self.conv_store.advance_thread_epoch(thread_id, "seat_failover")
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
                        requester=requester,
                        turn_epoch=turn_epoch,
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
                        await finish_summary(
                            self.conv_store,
                            role_name,
                            requester,
                            thread_id,
                            user_message_id,
                            placeholder_id,
                            result.memory_digest,
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
                    current_prompt=raw_prompt,
                    role=role,
                    context=context_section(context_blocks),
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
                    requester=requester,
                    turn_epoch=turn_epoch,
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
                    await finish_summary(
                        self.conv_store,
                        role_name,
                        requester,
                        thread_id,
                        user_message_id,
                        placeholder_id,
                        result.memory_digest,
                    )
                    return result

                best_failed = choose_preferred_chat_failure(best_failed, candidate, result)
                log.warning(
                    "Turn attempt failed on %s: %s; falling back",
                    candidate,
                    result.failure_class,
                )
                fallback_steps += 1
                metrics.record_fallback()

            if best_failed is not None:
                failed_provider, failed_result = best_failed
                await record_chat_failure_if_pending(
                    self.conv_store,
                    thread_id,
                    placeholder_id,
                    actor=role.name if role else failed_provider,
                    failure_class=failed_result.failure_class,
                    retryable=failed_result.retryable,
                    detail=failed_result.remediation,
                    error=failed_result.error,
                )
                return failed_result

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
        requester: Principal | None = None,
        turn_epoch: int = 0,
    ) -> ChatTurnResult:
        scratch_dir = thread_scratch_dir(thread_id)
        bus = get_thread_bus()

        t_start = time.monotonic()
        t_first_token: float | None = None

        captured_session_id = session_id
        stdout_text: list[str] = []
        stderr_text: list[str] = []

        # The version probe may spawn ``--version`` once per binary; keep it off the event loop (#1680).
        gate = await asyncio.to_thread(version_gate, getattr(adapter, "executable", ""))
        if gate is not None:
            return ChatTurnResult(ok=False, **gate.to_dict())
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
            return ChatTurnResult(
                ok=False,
                failure_class=failure_class,
                retryable=False,
                remediation=remediation,
                error=str(exc),
            )
        env = {**os.environ, **adapter.runtime_env()}

        # chat_stdin() is non-None only when the prompt is too long for argv (#2744).
        proc = self._spawn_cli_process(cmd=cmd, cwd=scratch_dir, env=env, stdin_text=adapter.chat_stdin(prompt))

        # Stream stdout lines incrementally and publish tokens live (SC-B1-G10)
        stream_out = await stream_turn_output(
            reader=LiveProcessReader(proc),
            adapter=adapter,
            bus=bus,
            thread_id=thread_id,
            placeholder_id=placeholder_id,
            hide_memory_summary=bool(summary_instruction(role.name if role else "", requester)),
            epoch=turn_epoch,
            is_current_epoch=lambda: self.conv_store.is_current_thread_epoch(thread_id, turn_epoch),
        )
        stdout_text = stream_out.stdout_lines
        stderr_text = stream_out.stderr_lines
        returncode = stream_out.returncode
        if stream_out.session_id:
            captured_session_id = stream_out.session_id
        t_first_token = stream_out.t_first_token

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
            return ChatTurnResult(
                ok=False,
                failure_class=classified.failure_class,
                retryable=classified.retryable,
                remediation=classified.remediation,
                error=classified.error,
            )

        # Parse structured reply contract
        reply_text, memory_digest = stream_out.reply_text or raw_combined, None
        if summary_instruction(role.name if role else "", requester):
            reply_text, memory_digest = extract_summary(reply_text)
        parsed = parse_reply(reply_text, role=role)

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
            "epoch": turn_epoch,
            **({"replayed_history": True} if replayed_history else {}),
            **({"handoff": parsed.handoff} if parsed.handoff else {}),
            **({"question": parsed.question} if parsed.question else {}),
        }

        completed = self.conv_store.update_message(
            placeholder_id,
            turn_epoch=turn_epoch,
            kind="text",
            delivery="complete",
            body_md=parsed.reply,
            meta=msg_meta,
        )
        if completed is None:
            log.warning("Dropped completion from stale CLI turn for thread %s (epoch %d)", thread_id, turn_epoch)
            return ChatTurnResult(
                ok=False,
                failure_class="stale_turn_epoch",
                retryable=True,
                remediation="This response was superseded by a seat handoff; retry the message.",
            )

        # Each proposed action becomes an ActionCard message after the reply (#1547)
        created_proposals: list[ProposedAction] = []
        for action in parsed.actions:
            try:
                prop = await post_proposal(
                    self.conv_store,
                    bus,
                    thread_id=thread_id,
                    author=role.name if role else "assistant",
                    action=action.action,
                    params=action.params,
                    reason=action.reason,
                    epoch=turn_epoch,
                )
                created_proposals.append(action)
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to persist action proposal %s: %s", action.action, exc)
                continue
            if requester is not None and prop.state == "proposed" and request_approves(requester, action.action):
                await run_for_requester(self.conv_store, bus, prop.id, requester)

        # Persist provider session id on thread metadata
        if captured_session_id and (th := self.conv_store.get_thread(thread_id)):
            cur_sessions = {**th.meta.get("provider_sessions", {}), adapter.provider_id: captured_session_id}
            self.conv_store.update_thread(thread_id, meta={**th.meta, "provider_sessions": cur_sessions})

        if memory_digest:
            th = self.conv_store.get_thread(thread_id)
            if th and self.conv_store.is_current_thread_epoch(thread_id, turn_epoch):
                self.conv_store.update_thread(
                    thread_id, meta={**th.meta, "handoff_digest": memory_digest["summary"][:1000]}
                )

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
            memory_digest=memory_digest,
        )
