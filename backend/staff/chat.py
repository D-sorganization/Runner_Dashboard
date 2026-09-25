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

import asyncio
import logging
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from staff.adapters import ADAPTERS, ProviderAdapter, get_adapter
from staff.chat_history import (
    DEFAULT_TOKEN_BUDGET,
    extract_session_id,
    format_history_replay,
)
from staff.classifier import classify_run_failure
from staff.conversations import (
    ConversationStore,
    get_conversation_store,
)
from staff.reply_contract import (
    ProposedAction,
    parse_reply,
)
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
    "extract_session_id",
    "format_history_replay",
    "get_chat_pool",
    "run_chat_turn_in_background",
]

# Concurrency defaults
DEFAULT_MAX_CHAT_TURNS = int(os.environ.get("STAFF_MAX_CHAT_TURNS", "4"))
DEFAULT_BARB_RESERVED_SLOTS = 1


class ChatConcurrencyPool:
    """Bounded concurrency pool for chat turns with reserved slots for Barb (SC-C6)."""

    def __init__(
        self, max_concurrency: int = DEFAULT_MAX_CHAT_TURNS, barb_reserved: int = DEFAULT_BARB_RESERVED_SLOTS
    ) -> None:
        self.max_concurrency = max(1, max_concurrency)
        self.barb_reserved = min(max(0, barb_reserved), self.max_concurrency - 1)
        self._active: int = 0
        self._lock = threading.Lock()

    def try_acquire(self, role: str) -> bool:
        """Attempt to acquire a chat slot for ``role``.

        Barb can use any slot up to ``max_concurrency``.
        Other roles can only acquire if active < (max_concurrency - barb_reserved).
        """
        with self._lock:
            if role.lower() == "barb":
                if self._active < self.max_concurrency:
                    self._active += 1
                    return True
                return False

            if self._active < (self.max_concurrency - self.barb_reserved):
                self._active += 1
                return True
            return False

    def release(self, role: str) -> None:
        """Release an acquired chat slot."""
        with self._lock:
            if self._active > 0:
                self._active -= 1


# Global chat concurrency pool singleton
_CHAT_POOL: ChatConcurrencyPool | None = None


def get_chat_pool() -> ChatConcurrencyPool:
    global _CHAT_POOL
    if _CHAT_POOL is None:
        _CHAT_POOL = ChatConcurrencyPool()
    return _CHAT_POOL


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
    ) -> None:
        self.conv_store = conv_store or get_conversation_store()
        self.adapters = adapters if adapters is not None else ADAPTERS
        self.pool = pool or get_chat_pool()

    def _spawn_cli_process(
        self,
        cmd: list[str],
        cwd: str,
        env: dict[str, str],
    ) -> subprocess.Popen[str]:
        """Spawn the provider CLI subprocess in read-only scratch mode."""
        return subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

    async def execute_turn(
        self,
        thread_id: str,
        user_message_id: str,
        placeholder_id: str,
        role_name: str,
        provider: str | None = None,
    ) -> ChatTurnResult:
        """Execute a conversational chat turn, streaming tokens and persisting reply."""
        roles = load_roles()
        role = roles.get(role_name)

        target_provider = provider or (role.provider if role and role.provider else "claude")
        adapter = self.adapters.get(target_provider) or get_adapter(target_provider)

        thread = self.conv_store.get_thread(thread_id)
        if not thread:
            return ChatTurnResult(ok=False, failure_class="thread_not_found", remediation="Thread not found.")

        user_msg = self.conv_store.get_message(user_message_id)
        prompt_text = user_msg.body_md if user_msg else ""

        # Acquire concurrency slot (or proceed if unavailable with degraded log)
        acquired = self.pool.try_acquire(role_name)
        if not acquired:
            log.warning("Chat concurrency limit reached for role %s; executing in fallback queue", role_name)

        try:
            # Check existing provider session
            existing_session = thread.meta.get("provider_sessions", {}).get(target_provider)

            # Try execution with session resume if available
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
                    return result
                log.info(
                    "Session resume failed for %s on thread %s; falling back to replay", target_provider, thread_id
                )

            # Fallback to history replay within token budget
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
            )
            result.replayed_history = was_fallback
            return result
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
    ) -> ChatTurnResult:
        scratch_dir = tempfile.mkdtemp(prefix="staff_chat_")
        bus = get_thread_bus()

        t_start = time.monotonic()
        t_first_token: float | None = None

        captured_session_id = session_id
        stdout_text: list[str] = []
        stderr_text: list[str] = []

        try:
            cmd = adapter.chat_argv(
                prompt=prompt,
                workdir=scratch_dir,
                model=role.model if role else None,
                session_id=session_id,
            )
            env = {**os.environ, **adapter.runtime_env()}

            proc = self._spawn_cli_process(cmd=cmd, cwd=scratch_dir, env=env)

            # Stream stdout lines
            loop = asyncio.get_running_loop()

            def _read_output() -> tuple[list[str], list[str], int]:
                out_lines: list[str] = []
                if proc.stdout:
                    for line in proc.stdout:
                        out_lines.append(line)
                err_lines: list[str] = []
                if proc.stderr:
                    for eline in proc.stderr:
                        err_lines.append(eline)
                rc: Any = None
                if callable(getattr(proc, "wait", None)):
                    try:
                        rc = proc.wait()
                    except Exception:  # noqa: BLE001
                        rc = None
                if not isinstance(rc, int):
                    rc = getattr(proc, "returncode", None)
                if not isinstance(rc, int):
                    rc = 0
                return out_lines, err_lines, rc

            lines, err_lines, returncode = await loop.run_in_executor(None, _read_output)
            stderr_text = err_lines

            deltas: list[str] = []
            for line in lines:
                stdout_text.append(line)
                event = adapter.parse_line(line)

                # Check session id extraction
                detected_sid = extract_session_id(adapter.provider_id, event, raw_line=line)
                if detected_sid:
                    captured_session_id = detected_sid

                delta = event.get("text", "")
                if delta:
                    deltas.append(delta)
                    if t_first_token is None:
                        t_first_token = time.monotonic()
                    # Stream token delta immediately over SSE bus
                    await bus.publish_token(thread_id, placeholder_id, delta)

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
                if not is_resume:
                    err_detail = classified.remediation or classified.error or "Failed to complete reply"
                    actor = role.name if role else adapter.provider_id
                    self.conv_store.update_message(
                        placeholder_id,
                        kind="error",
                        delivery="failed",
                        body_md=f"Error from {actor}: {err_detail}",
                        meta={
                            "failure_class": classified.failure_class,
                            "retryable": classified.retryable,
                            "actions": [{"name": "retry", "label": "Retry"}],
                            "error": classified.error,
                        },
                    )
                    err_msg = self.conv_store.get_message(placeholder_id)
                    if err_msg:
                        await bus.publish_message(thread_id, err_msg.to_dict())

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
            # Clean up scratch directory
            try:
                import shutil

                shutil.rmtree(scratch_dir, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass


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
        log.error("Unhandled error during chat turn for thread %s: %s", thread_id, exc, exc_info=True)
