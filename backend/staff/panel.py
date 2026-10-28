"""Expert panel engine (#1634, epic #1633): experts take turns, then a moderator synthesises.

Turns run one at a time. Every expert prompt carries the discussion so far, so each
speaker answers the others rather than the topic alone. A debate ends early when every
expert in a round ends with ``STANCE: agree``; a brainstorm always runs its rounds. A
failed or timed-out turn is recorded as that expert's missed turn and the panel goes on.
State lives on the thread (``meta.panel``) and on each turn message (``meta.panel_*``).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import provider_switch
from staff import cli_projects
from staff.adapter_policies import ChatReadOnlyUnsupportedError
from staff.adapters import get_adapter
from staff.barb_continuity import get_barb_continuity_store
from staff.chat_streaming import LiveProcessReader, spawn_cli_process, stream_turn_output
from staff.conversation_models import ThreadRecord
from staff.conversations import ConversationStore, get_conversation_store
from staff.panel_grounding import GROUNDING_DIR, GROUNDING_TOOLS, build_snapshot, remove_snapshot
from staff.panel_hook_errors import hook_error_reason, is_hook_error_reply
from staff.panel_models import (
    CODEBASE_REPOS,
    DEFAULT_TURN_TIMEOUT_SECONDS,
    MODERATOR_NAME,
    PanelCreateRequest,
    PanelSpeaker,
    TurnOutcome,
)
from staff.panel_prompts import (
    build_synthesis_prompt,
    build_turn_prompt,
    compute_tally,
    estimate_panel_cost,
    parse_stance,
)
from staff.panel_scratch import prepare_panel_turn_scratch, remove_panel_turn_scratch
from staff.thread_bus import get_thread_bus

log = logging.getLogger("dashboard.staff.panel")

__all__ = [
    "PanelSpeaker",
    "TurnOutcome",
    "TurnRunner",
    "compute_tally",
    "active_panel_count",
    "default_turn_runner",
    "estimate_panel_cost",
    "list_panel_summaries",
    "panel_result",
    "panel_summary",
    "parse_stance",
    "run_panel",
    "record_panel_turnover",
    "start_panel_thread",
]

TurnRunner = Callable[[PanelSpeaker, str, str, str], Awaitable[TurnOutcome]]
_ACTIVE_PANELS: set[str] = set()


def active_panel_count() -> int:
    """Number of panels whose engine is running in this process."""
    return len(_ACTIVE_PANELS)


def _speaker(expert: Any) -> PanelSpeaker:
    return PanelSpeaker(name=expert.name, perspective=expert.perspective, provider=expert.provider, model=expert.model)


def _preload_codebase_context(request: PanelCreateRequest) -> str:
    """Read an available pack once; absent packs never trigger a cold repository scan."""
    from knowledge_pack.cartographer import preload_panel_context
    from staff.knowledge_refresh import get_knowledge_dir
    from staff.workspace import find_repo_checkout

    if not request.preload_codebase or request.pack_id is None:
        return ""
    if not (get_knowledge_dir() / "cartographer.sqlite").is_file():
        return ""
    roots = {}
    for repo in request.repos or CODEBASE_REPOS:
        root = find_repo_checkout(repo)
        if root is not None:
            roots[repo] = root
    return preload_panel_context(roots, request.topic, max_tokens=800) if roots else ""


def start_panel_thread(request: PanelCreateRequest, created_by: str, store: ConversationStore) -> ThreadRecord:
    """Create the ``kind="panel"`` thread and post the topic as the opening user message."""
    names = [e.name for e in request.experts]
    thread = store.create_thread(
        title=request.title or f"Panel: {request.topic[:80]}",
        kind="panel",
        participants=[*names, MODERATOR_NAME],
        created_by=created_by,
        meta={
            "panel": {
                "status": "running",
                "mode": request.mode,
                "rounds": request.rounds,
                "rounds_used": 0,
                "consensus": False,
                "experts": [e.model_dump() for e in request.experts],
                "synthesis_message_id": None,
                "codebase_context": _preload_codebase_context(request),
            }
        },
    )
    store.add_message(
        thread.id, author_kind="user", author=created_by, body_md=request.topic, meta={"panel_topic": True}
    )
    return thread


def _set_panel_state(store: ConversationStore, thread_id: str, **fields: Any) -> None:
    thread = store.get_thread(thread_id)
    assert thread is not None, f"panel thread {thread_id} vanished"  # noqa: S101
    meta = dict(thread.meta)
    meta["panel"] = {**meta.get("panel", {}), **fields}
    store.update_thread(thread_id, meta=meta)


async def _publish(store: ConversationStore, message_id: str) -> None:
    msg = store.get_message(message_id)
    if msg is not None:
        await get_thread_bus().publish_message(msg.thread_id, msg.to_dict())


async def _speak(
    store: ConversationStore,
    thread_id: str,
    speaker: PanelSpeaker,
    prompt: str,
    meta: dict[str, Any],
    runner: TurnRunner,
    timeout: float,
) -> dict[str, Any]:
    """Run one turn into its own message and return the turn record (status ok/error/timeout)."""
    placeholder = store.add_message(
        thread_id, author_kind="role", author=speaker.name, body_md="", meta=meta, delivery="streaming"
    )
    await _publish(store, placeholder.id)
    try:
        outcome = await asyncio.wait_for(runner(speaker, prompt, thread_id, placeholder.id), timeout=timeout)
        status, text, error = ("ok", outcome.text, None) if outcome.ok else ("error", "", outcome.error or "failed")
    except TimeoutError:
        status, text, error = "timeout", "", f"no reply within {timeout:.0f} s"
    except Exception as exc:  # noqa: BLE001 — one seat failing must not end the panel
        log.warning("panel turn by %s failed: %s", speaker.name, exc)
        status, text, error = "error", "", str(exc)
    stance, position = parse_stance(text) if status == "ok" else (None, None)
    final_meta = {**meta, "panel_status": status, "panel_stance": stance, "panel_position": position}
    if error:
        final_meta["error"] = error
    store.update_message(
        placeholder.id,
        kind="text" if status == "ok" else "error",
        body_md=text if status == "ok" else f"{speaker.name} missed this turn: {error}",
        meta=final_meta,
        delivery="complete" if status == "ok" else "failed",
    )
    await _publish(store, placeholder.id)
    return {"message_id": placeholder.id, "status": status, "text": text, "stance": stance, "error": error}


async def _prepare_grounding(request: PanelCreateRequest) -> str | None:
    """Snapshot the named repos for read-only seat access; None when not asked for or nothing could be built."""
    from staff.workspace import find_repo_checkout

    if not request.repos or not request.ground_repos:
        return None
    roots = {repo: root for repo in request.repos if (root := find_repo_checkout(repo)) is not None}
    if not roots:
        log.warning("panel grounding requested but no checkout found for %s", request.repos)
        return None
    task = asyncio.create_task(asyncio.to_thread(build_snapshot, roots))
    try:
        return await task
    except asyncio.CancelledError:

        def _drop_orphan(fut: asyncio.Task[str | None]) -> None:
            try:
                path = fut.result()
            except (asyncio.CancelledError, Exception):
                return
            if path:
                remove_snapshot(path)

        task.add_done_callback(_drop_orphan)
        raise
    except Exception as exc:  # noqa: BLE001 — a grounding problem degrades the panel, never fails it
        log.warning("panel grounding snapshot failed: %s", exc)
        return None


def _grounded_repo_names(grounding_dir: str | None) -> tuple[str, ...]:
    if not grounding_dir:
        return ()
    return tuple(sorted(p.name for p in Path(grounding_dir).iterdir() if p.is_dir()))


def record_panel_turnover(thread_id: str, topic: str, consensus: bool, rounds_used: int, ok: bool) -> None:
    """Append a Barb turnover note (scope ``panel``) for a finished panel. Never raises."""
    try:
        outcome = "consensus reached" if consensus else "no consensus"
        get_barb_continuity_store().append_turnover(
            scope="panel",
            author="panel-engine",
            summary=(
                f"Panel {'complete' if ok else 'failed'} ({outcome}, {rounds_used} round(s)) on: "
                f"{topic[:200]}. Thread {thread_id}."
            ),
            completed=[f"panel thread {thread_id}"] if ok else [],
            next_actions=[f"Read the synthesis at /api/v1/staff/panels/{thread_id}"],
        )
    except Exception as exc:  # noqa: BLE001 — a turnover problem must never fail a panel
        log.warning("panel %s turnover note not recorded: %s", thread_id, exc)


async def run_panel(
    thread_id: str,
    request: PanelCreateRequest,
    runner: TurnRunner | None = None,
    turn_timeout: float = DEFAULT_TURN_TIMEOUT_SECONDS,
) -> None:
    """Run every round, then the moderator. Post: ``meta.panel.status`` is ``complete`` or ``failed``."""
    run = runner or default_turn_runner
    store = get_conversation_store()
    thread = store.get_thread(thread_id)
    codebase_context = str(thread.meta.get("panel", {}).get("codebase_context", "")) if thread else ""
    turns: list[dict[str, Any]] = []
    rounds_used, consensus = 0, False
    grounding_dir: str | None = None
    grounding_token: contextvars.Token[str | None] | None = None
    _ACTIVE_PANELS.add(thread_id)
    try:
        grounding_dir = await _prepare_grounding(request)
        grounding_token = GROUNDING_DIR.set(grounding_dir)
        grounded_repos = _grounded_repo_names(grounding_dir)
        try:  # never awaited: the sweep must not hold up or abandon the panel (#1683)
            cli_projects.start_panel_sweep(os.environ)
        except Exception as exc:  # noqa: BLE001 — sweep problem must never fail a panel
            log.warning("failed to start the stale panel project sweep: %s", exc)
        for round_no in range(1, request.rounds + 1):
            round_turns = []
            for expert in request.experts:
                speaker = _speaker(expert)
                prompt = build_turn_prompt(request, speaker, round_no, turns, codebase_context, grounded_repos)
                meta = {"is_panel_turn": True, "panel_round": round_no, "panel_expert": speaker.name}
                record = await _speak(store, thread_id, speaker, prompt, meta, run, turn_timeout)
                record.update(round=round_no, expert=speaker.name)
                turns.append(record)
                round_turns.append(record)
            rounds_used = round_no
            consensus = all(t["stance"] == "agree" for t in round_turns)
            _set_panel_state(store, thread_id, rounds_used=rounds_used, consensus=consensus)
            if consensus and request.mode == "debate":
                break

        tally = compute_tally([e.name for e in request.experts], turns, request.quorum)
        _set_panel_state(store, thread_id, **tally)
        moderator = PanelSpeaker(
            MODERATOR_NAME, "neutral synthesis", request.moderator_provider, request.moderator_model
        )
        prompt = build_synthesis_prompt(request, turns, rounds_used, consensus, codebase_context, tally)
        meta = {"is_panel_synthesis": True, "panel_expert": MODERATOR_NAME}
        synthesis = await _speak(store, thread_id, moderator, prompt, meta, run, turn_timeout)
        synthesis_ok = synthesis["status"] == "ok"
        expert_ok = any(t.get("status") == "ok" for t in turns)
        seat_errors = sum(1 for t in turns if t.get("status") != "ok")
        if synthesis_ok:
            panel_status = "complete"
        elif expert_ok:
            panel_status = "complete"
        else:
            panel_status = "failed"
        _set_panel_state(
            store,
            thread_id,
            status=panel_status,
            synthesis_message_id=synthesis["message_id"],
            synthesis_ok=synthesis_ok,
            seat_errors=seat_errors if seat_errors else None,
        )
        record_panel_turnover(thread_id, request.topic, consensus, rounds_used, panel_status == "complete")
    except Exception as exc:  # noqa: BLE001 — record the failure on the thread, never leave it "running"
        log.exception("panel %s failed", thread_id)
        _set_panel_state(store, thread_id, status="failed", error=str(exc))
    finally:
        _ACTIVE_PANELS.discard(thread_id)
        if grounding_token is not None:
            GROUNDING_DIR.reset(grounding_token)
        if grounding_dir:
            cli_projects.remove_turn_project(grounding_dir, os.environ)
            remove_snapshot(grounding_dir)


def panel_summary(thread: ThreadRecord) -> dict[str, Any]:
    """Compact panel row for list endpoints. Pre: ``thread.kind == \"panel\"``."""
    assert thread.kind == "panel", f"panel_summary requires kind=panel, got {thread.kind!r}"  # noqa: S101
    state = dict(thread.meta.get("panel", {}))
    return {
        "thread_id": thread.id,
        "title": thread.title,
        "status": state.get("status", "running"),
        "mode": state.get("mode"),
        "rounds": state.get("rounds"),
        "rounds_used": state.get("rounds_used", 0),
        "consensus": bool(state.get("consensus")),
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
        "last_message_at": thread.last_message_at,
    }


def list_panel_summaries(store: ConversationStore, *, limit: int = 200) -> list[dict[str, Any]]:
    """Recent panel threads, newest ``updated_at`` first. Pre: ``1 <= limit <= 200``."""
    assert 1 <= limit <= 200, f"list_panel_summaries limit out of range: {limit}"  # noqa: S101
    threads = store.list_threads(kind="panel", limit=limit)
    return [panel_summary(t) for t in threads]


def panel_result(store: ConversationStore, thread_id: str) -> dict[str, Any] | None:
    """The panel's state for the API: turns in order, stances, consensus and synthesis. None if not a panel."""
    thread = store.get_thread(thread_id)
    if thread is None or thread.kind != "panel":
        return None
    state = dict(thread.meta.get("panel", {}))
    turns, synthesis = [], None
    for msg in store.list_messages(thread_id, limit=500):
        meta = msg.meta or {}
        if meta.get("is_panel_turn"):
            turns.append(
                {
                    "message_id": msg.id,
                    "round": meta.get("panel_round"),
                    "expert": meta.get("panel_expert"),
                    "status": meta.get("panel_status", "running"),
                    "stance": meta.get("panel_stance"),
                    "position": meta.get("panel_position"),
                    "body_md": msg.body_md,
                    "error": meta.get("error"),
                }
            )
        elif msg.id == state.get("synthesis_message_id"):
            synthesis = msg.body_md
    seats = [str(e.get("name")) for e in state.get("experts", [])]
    tally = compute_tally(seats, turns, state.get("quorum")) if seats else {}
    return {
        **tally,
        "thread_id": thread_id,
        "title": thread.title,
        "status": state.get("status", "running"),
        "mode": state.get("mode"),
        "rounds": state.get("rounds"),
        "rounds_used": state.get("rounds_used", 0),
        "consensus": bool(state.get("consensus")),
        "experts": state.get("experts", []),
        "turns": turns,
        "synthesis": synthesis,
    }


# ── the real turn runner ────────────────────────────────────────────────────
class _NullBus:
    """Null bus that discards all published tokens."""

    async def publish_token(self, *args: Any, **kwargs: Any) -> None:
        pass


_NULL_BUS = _NullBus()


async def default_turn_runner(
    speaker: PanelSpeaker, prompt: str, thread_id: str, message_id: str | None
) -> TurnOutcome:
    """Run one read-only CLI chat turn, streaming tokens into ``message_id`` (or no tokens when None).

    Post: the CLI process is gone, its scratch directory removed, and its CLI project folder
    removed, whether the turn finished, failed, timed out or was cancelled.
    """
    if provider_switch.is_disabled(speaker.provider):  # #1597 holds on panel and seat turns
        return TurnOutcome(ok=False, error=f"{speaker.provider} {provider_switch.DISABLED_DETAIL}")
    try:
        adapter = get_adapter(speaker.provider)
    except KeyError:
        return TurnOutcome(ok=False, error=f"unknown provider {speaker.provider!r}")
    scratch_dir = prepare_panel_turn_scratch()
    grounding_dir = GROUNDING_DIR.get() if speaker.name != MODERATOR_NAME else None
    workdir = grounding_dir or scratch_dir  # a snapshot is shared by the panel and removed by run_panel
    tools = GROUNDING_TOOLS if grounding_dir else ()
    env: dict[str, str] = {**os.environ, "STAFF_PANEL_TURN": "1"}
    proc = None
    try:
        try:
            cmd = adapter.chat_argv(prompt=prompt, workdir=workdir, model=speaker.model, read_only_tools=tools)
        except (ChatReadOnlyUnsupportedError, ValueError) as exc:
            return TurnOutcome(ok=False, error=str(exc))
        env.update(adapter.runtime_env())
        stdin_text = adapter.chat_stdin(prompt)
        extra: dict[str, Any] = {"stdin_text": stdin_text} if isinstance(stdin_text, str) else {}
        proc = spawn_cli_process(cmd=cmd, cwd=workdir, env=env, **extra)
        bus = get_thread_bus() if message_id is not None else _NULL_BUS
        out = await stream_turn_output(
            reader=LiveProcessReader(proc),
            adapter=adapter,
            bus=bus,
            thread_id=thread_id,
            placeholder_id=message_id or "",
        )
        text = out.reply_text.strip()
        if is_hook_error_reply(text):
            return TurnOutcome(ok=False, error=hook_error_reason(text))
        if out.returncode != 0 or not text:
            tail = "".join(out.stderr_lines[-5:]).strip()[-400:]
            return TurnOutcome(ok=False, error=f"exit {out.returncode}: {tail or 'empty reply'}")
        return TurnOutcome(ok=True, text=text)
    finally:
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.kill()
        remove_panel_turn_scratch(scratch_dir)
        cli_projects.remove_turn_project(scratch_dir, env)
