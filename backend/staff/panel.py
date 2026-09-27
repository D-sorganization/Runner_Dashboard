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
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from typing import Any

from staff.adapter_policies import ChatReadOnlyUnsupportedError
from staff.adapters import get_adapter
from staff.chat_streaming import LiveProcessReader, spawn_cli_process, stream_turn_output
from staff.conversation_models import ThreadRecord
from staff.conversations import ConversationStore, get_conversation_store
from staff.group_models import GroupCostEstimate, get_group_threshold, lookup_seat_price
from staff.panel_models import (
    DEFAULT_TURN_TIMEOUT_SECONDS,
    MODERATOR_NAME,
    STANCES,
    PanelCreateRequest,
    PanelSpeaker,
    TurnOutcome,
)
from staff.thread_bus import get_thread_bus

log = logging.getLogger("dashboard.staff.panel")

__all__ = [
    "PanelSpeaker",
    "TurnOutcome",
    "TurnRunner",
    "active_panel_count",
    "default_turn_runner",
    "estimate_panel_cost",
    "panel_result",
    "parse_stance",
    "run_panel",
    "start_panel_thread",
]

TurnRunner = Callable[[PanelSpeaker, str, str, str], Awaitable[TurnOutcome]]
TURN_EXCERPT_CHARS = 3000  # each earlier turn is quoted up to this length in later prompts
_ACTIVE_PANELS: set[str] = set()
_STANCE_RE = re.compile(r"^[\s*_>-]*stance[\s*_]*:[\s*_]*([a-z]+)", re.IGNORECASE | re.MULTILINE)
_POSITION_RE = re.compile(r"^[\s*_>-]*position[\s*_]*:[\s*_]*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_STANCE_ALIASES = {
    "agree": "agree",
    "partly": "partly",
    "partially": "partly",
    "partial": "partly",
    "disagree": "disagree",
}

_MODE_BRIEF = {
    "debate": (
        "This is a debate. Argue from your perspective, answer the other panelists by name, "
        "concede points that are right, and move the panel toward a defensible consensus."
    ),
    "brainstorm": (
        "This is a brainstorm. Build on the other panelists' ideas, add new ones from your "
        "perspective, and flag the most promising directions. Quantity and variety first."
    ),
}


def active_panel_count() -> int:
    """Number of panels whose engine is running in this process."""
    return len(_ACTIVE_PANELS)


# ── parsing and prompts ─────────────────────────────────────────────────────
def parse_stance(text: str) -> tuple[str | None, str | None]:
    """Return the last ``STANCE:`` (normalised to agree/partly/disagree) and ``POSITION:`` in ``text``."""
    stances = _STANCE_RE.findall(text or "")
    stance = _STANCE_ALIASES.get(stances[-1].lower()) if stances else None
    positions = _POSITION_RE.findall(text or "")
    position = positions[-1].strip(" *_") if positions else None
    if stance is None:
        return None, None
    assert stance in STANCES  # noqa: S101
    return stance, position or None


def _speaker(expert: Any) -> PanelSpeaker:
    return PanelSpeaker(name=expert.name, perspective=expert.perspective, provider=expert.provider, model=expert.model)


def _transcript(turns: list[dict[str, Any]]) -> str:
    if not turns:
        return "(no one has spoken yet; you open the discussion)"
    parts = []
    for t in turns:
        text = t["text"] if t["status"] == "ok" else f"[missed turn: {t['status']}]"
        if len(text) > TURN_EXCERPT_CHARS:
            text = text[:TURN_EXCERPT_CHARS] + " [...]"
        parts.append(f"### Round {t['round']} — {t['expert']}\n{text}")
    return "\n\n".join(parts)


def build_turn_prompt(
    request: PanelCreateRequest, speaker: PanelSpeaker, round_no: int, turns: list[dict[str, Any]]
) -> str:
    """The prompt for one expert turn: persona, mode, round, topic and the whole discussion so far."""
    others = ", ".join(f"{e.name} ({e.perspective})" for e in request.experts if e.name != speaker.name)
    return (
        f"You are {speaker.name}, one of {len(request.experts)} experts on a discussion panel.\n"
        f"Your perspective: {speaker.perspective}\n"
        f"The other panelists: {others}.\n\n"
        f"{_MODE_BRIEF[request.mode]}\n\n"
        f"This is round {round_no} of {request.rounds}. Keep your turn under 300 words. "
        "Do not use tools or edit files; answer from your own knowledge.\n\n"
        f"## Topic\n{request.topic}\n\n"
        f"## Discussion so far\n{_transcript(turns)}\n\n"
        "End your turn with exactly these two lines:\n"
        "STANCE: agree | partly | disagree  (with the emerging panel consensus)\n"
        "POSITION: <your current position in one sentence>"
    )


def build_synthesis_prompt(
    request: PanelCreateRequest, turns: list[dict[str, Any]], rounds_used: int, consensus: bool
) -> str:
    """The moderator's prompt: the whole discussion and the sections the synthesis must have."""
    if consensus:
        outcome = f"The panel reached consensus in round {rounds_used} of {request.rounds}."
    else:
        outcome = f"The panel ran {rounds_used} of {request.rounds} rounds without unanimous agreement."
    if request.mode == "brainstorm":
        sections = "**Top ideas** (ranked), **Themes**, **Open questions**, **Next steps**"
    else:
        sections = (
            "**Consensus**, **Agreed points**, **Open disagreements** (who holds which view), "
            "**Recommendation**, **Confidence** (low / medium / high, with why)"
        )
    return (
        f"You are the {MODERATOR_NAME} of an expert panel. {outcome}\n"
        "Do not use tools or edit files. Summarise the discussion faithfully; do not invent "
        "positions no panelist took, and say plainly where the panel disagreed.\n\n"
        f"## Topic\n{request.topic}\n\n"
        f"## Discussion\n{_transcript(turns)}\n\n"
        f"Write the synthesis in Markdown with these sections: {sections}."
    )


# ── cost ────────────────────────────────────────────────────────────────────
def estimate_panel_cost(request: PanelCreateRequest) -> GroupCostEstimate:
    """Estimated USD for the whole panel. Each prompt grows by the turns before it (~400 tokens each)."""
    base_tokens = 350 + len(request.topic) // 4
    per_turn_tokens, turn_output = 400, 600
    costs: dict[str, float] = {}
    turn_index = 0
    for _ in range(request.rounds):
        for expert in request.experts:
            price = lookup_seat_price(expert.provider, expert.model or "")
            prompt_tokens = base_tokens + per_turn_tokens * turn_index
            costs[expert.name] = costs.get(expert.name, 0.0) + price.cost(prompt_tokens, turn_output)
            turn_index += 1
    moderator_price = lookup_seat_price(request.moderator_provider, request.moderator_model or "")
    costs[MODERATOR_NAME] = moderator_price.cost(base_tokens + per_turn_tokens * turn_index, 1000)
    total = sum(costs.values())
    threshold = get_group_threshold()
    exceeds = total >= threshold
    warning = (
        f"Estimated panel cost ${total:.2f} exceeds threshold ${threshold:.2f}. Set 'confirm_cost: true' to proceed."
        if exceeds
        else None
    )
    return GroupCostEstimate("panel", total, costs, exceeds, threshold, warning)


# ── thread state ────────────────────────────────────────────────────────────
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
    return {"message_id": placeholder.id, "status": status, "text": text, "stance": stance}


async def run_panel(
    thread_id: str,
    request: PanelCreateRequest,
    runner: TurnRunner | None = None,
    turn_timeout: float = DEFAULT_TURN_TIMEOUT_SECONDS,
) -> None:
    """Run every round, then the moderator. Post: ``meta.panel.status`` is ``complete`` or ``failed``."""
    run = runner or default_turn_runner
    store = get_conversation_store()
    turns: list[dict[str, Any]] = []
    rounds_used, consensus = 0, False
    _ACTIVE_PANELS.add(thread_id)
    try:
        for round_no in range(1, request.rounds + 1):
            round_turns = []
            for expert in request.experts:
                speaker = _speaker(expert)
                prompt = build_turn_prompt(request, speaker, round_no, turns)
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

        moderator = PanelSpeaker(
            MODERATOR_NAME, "neutral synthesis", request.moderator_provider, request.moderator_model
        )
        prompt = build_synthesis_prompt(request, turns, rounds_used, consensus)
        meta = {"is_panel_synthesis": True, "panel_expert": MODERATOR_NAME}
        synthesis = await _speak(store, thread_id, moderator, prompt, meta, run, turn_timeout)
        ok = synthesis["status"] == "ok"
        _set_panel_state(
            store,
            thread_id,
            status="complete" if ok else "failed",
            synthesis_message_id=synthesis["message_id"] if ok else None,
        )
    except Exception as exc:  # noqa: BLE001 — record the failure on the thread, never leave it "running"
        log.exception("panel %s failed", thread_id)
        _set_panel_state(store, thread_id, status="failed", error=str(exc))
    finally:
        _ACTIVE_PANELS.discard(thread_id)


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
                }
            )
        elif msg.id == state.get("synthesis_message_id"):
            synthesis = msg.body_md
    return {
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

    Post: the CLI process is gone and its scratch directory removed, whether the turn
    finished, failed, timed out or was cancelled.
    """
    try:
        adapter = get_adapter(speaker.provider)
    except KeyError:
        return TurnOutcome(ok=False, error=f"unknown provider {speaker.provider!r}")
    scratch_dir = tempfile.mkdtemp(prefix="staff_panel_")
    proc = None
    try:
        try:
            cmd = adapter.chat_argv(prompt=prompt, workdir=scratch_dir, model=speaker.model, read_only_tools=())
        except (ChatReadOnlyUnsupportedError, ValueError) as exc:
            return TurnOutcome(ok=False, error=str(exc))
        env = {**os.environ, **adapter.runtime_env()}
        proc = spawn_cli_process(cmd=cmd, cwd=scratch_dir, env=env)
        bus = get_thread_bus() if message_id is not None else _NULL_BUS
        out = await stream_turn_output(
            reader=LiveProcessReader(proc),
            adapter=adapter,
            bus=bus,
            thread_id=thread_id,
            placeholder_id=message_id or "",
        )
        text = out.reply_text.strip()
        if out.returncode != 0 or not text:
            tail = "".join(out.stderr_lines[-5:]).strip()[-400:]
            return TurnOutcome(ok=False, error=f"exit {out.returncode}: {tail or 'empty reply'}")
        return TurnOutcome(ok=True, text=text)
    finally:
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.kill()
        shutil.rmtree(scratch_dir, ignore_errors=True)
