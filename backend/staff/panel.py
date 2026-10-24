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
from pathlib import Path
from typing import Any

import provider_switch
from staff import cli_projects
from staff.adapter_policies import ChatReadOnlyUnsupportedError
from staff.adapters import get_adapter
from staff.barb_continuity import get_barb_continuity_store
from staff.chat_streaming import LiveProcessReader, spawn_cli_process, stream_turn_output
from staff.cli_projects import PANEL_SCRATCH_PREFIX
from staff.conversation_models import ThreadRecord
from staff.conversations import ConversationStore, get_conversation_store
from staff.group_models import GroupCostEstimate, get_group_threshold, lookup_seat_price
from staff.panel_grounding import GROUNDING_DIR, GROUNDING_TOOLS, build_snapshot, remove_snapshot
from staff.panel_models import (
    CODEBASE_REPOS,
    DEFAULT_TURN_TIMEOUT_SECONDS,
    MODERATOR_NAME,
    STANCES,
    PanelCreateRequest,
    PanelSpeaker,
    TurnOutcome,
    default_quorum,
)
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
    "panel_result",
    "parse_stance",
    "run_panel",
    "record_panel_turnover",
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


def _tool_brief(grounded_repos: tuple[str, ...]) -> str:
    if not grounded_repos:
        return "Do not use tools or edit files; answer from your own knowledge."
    return (
        "Your working directory holds read-only snapshots of origin/main, one folder per repository: "
        f"{', '.join(grounded_repos)}. Read files there to verify any code you cite and give file paths. "
        "Never edit files or run commands that change anything."
    )


def build_turn_prompt(
    request: PanelCreateRequest,
    speaker: PanelSpeaker,
    round_no: int,
    turns: list[dict[str, Any]],
    codebase_context: str = "",
    grounded_repos: tuple[str, ...] = (),
) -> str:
    """The prompt for one expert turn: persona, mode, round, topic and the whole discussion so far."""
    others = ", ".join(f"{e.name} ({e.perspective})" for e in request.experts if e.name != speaker.name)
    return (
        f"You are {speaker.name}, one of {len(request.experts)} experts on a discussion panel.\n"
        f"Your perspective: {speaker.perspective}\n"
        f"The other panelists: {others}.\n\n"
        f"{_MODE_BRIEF[request.mode]}\n\n"
        f"This is round {round_no} of {request.rounds}. Keep your turn under 300 words. "
        f"{_tool_brief(grounded_repos)}\n\n"
        f"## Topic\n{request.topic}\n\n"
        f"{codebase_context}\n\n"
        f"## Discussion so far\n{_transcript(turns)}\n\n"
        "End your turn with exactly these two lines:\n"
        "STANCE: agree | partly | disagree  (with the emerging panel consensus)\n"
        "POSITION: <your current position in one sentence>"
    )


def compute_tally(seats: list[str], turns: list[dict[str, Any]], quorum: int | None) -> dict[str, Any]:
    """Participation tally. A seat participated if at least one of its turns is ``ok``, else it abstained.

    Pre: ``turns`` are records with ``expert``, ``status`` and optionally ``error``.
    Post: ``tally`` counts add up to ``len(seats)``; each abstention carries a reason (the seat's last
    error, or "no turn recorded"); ``quorum_met`` is ``participated >= quorum`` (default: majority).
    """
    needed = quorum if quorum is not None else default_quorum(len(seats))
    abstentions: list[dict[str, str]] = []
    for seat in seats:
        mine = [t for t in turns if t.get("expert") == seat]
        if any(t.get("status") == "ok" for t in mine):
            continue
        reasons = [str(t.get("error") or t.get("status")) for t in mine]
        abstentions.append({"expert": seat, "reason": reasons[-1] if reasons else "no turn recorded"})
    participated = len(seats) - len(abstentions)
    return {
        "tally": {"participated": participated, "abstained": len(abstentions)},
        "abstentions": abstentions,
        "quorum": needed,
        "quorum_met": participated >= needed,
    }


def _tally_brief(tally: dict[str, Any], seats: int) -> str:
    counts = tally["tally"]
    lines = [
        f"Participation tally: {counts['participated']} of {seats} seats participated, {counts['abstained']} abstained."
    ]
    lines += [f"- {a['expert']} abstained: {a['reason']}" for a in tally["abstentions"]]
    lines.append(
        f"Quorum ({tally['quorum']} seats) was met."
        if tally["quorum_met"]
        else f"Quorum ({tally['quorum']} seats) was NOT met; say so prominently and lower your confidence."
    )
    lines.append(
        "Your synthesis does not override this tally: report it as is and never imply abstaining seats agreed."
    )
    return "\n".join(lines)


def build_synthesis_prompt(
    request: PanelCreateRequest,
    turns: list[dict[str, Any]],
    rounds_used: int,
    consensus: bool,
    codebase_context: str = "",
    tally: dict[str, Any] | None = None,
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
        f"{codebase_context}\n\n"
        f"## Discussion\n{_transcript(turns)}\n\n"
        f"{_tally_brief(tally, len(request.experts)) + chr(10) * 2 if tally else ''}"
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
    try:
        return await asyncio.to_thread(build_snapshot, roots)
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
    _ACTIVE_PANELS.add(thread_id)
    grounding_dir = await _prepare_grounding(request)
    grounding_token = GROUNDING_DIR.set(grounding_dir)
    grounded_repos = _grounded_repo_names(grounding_dir)
    try:
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
        ok = synthesis["status"] == "ok"
        _set_panel_state(
            store,
            thread_id,
            status="complete" if ok else "failed",
            synthesis_message_id=synthesis["message_id"] if ok else None,
        )
        record_panel_turnover(thread_id, request.topic, consensus, rounds_used, ok)
    except Exception as exc:  # noqa: BLE001 — record the failure on the thread, never leave it "running"
        log.exception("panel %s failed", thread_id)
        _set_panel_state(store, thread_id, status="failed", error=str(exc))
    finally:
        _ACTIVE_PANELS.discard(thread_id)
        GROUNDING_DIR.reset(grounding_token)
        if grounding_dir:
            cli_projects.remove_turn_project(grounding_dir, os.environ)
            remove_snapshot(grounding_dir)


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
    scratch_dir = tempfile.mkdtemp(prefix=PANEL_SCRATCH_PREFIX)
    grounding_dir = GROUNDING_DIR.get() if speaker.name != MODERATOR_NAME else None
    workdir = grounding_dir or scratch_dir  # a snapshot is shared by the panel and removed by run_panel
    tools = GROUNDING_TOOLS if grounding_dir else ()
    env: dict[str, str] = {**os.environ}
    proc = None
    try:
        try:
            cmd = adapter.chat_argv(prompt=prompt, workdir=workdir, model=speaker.model, read_only_tools=tools)
        except (ChatReadOnlyUnsupportedError, ValueError) as exc:
            return TurnOutcome(ok=False, error=str(exc))
        env.update(adapter.runtime_env())
        proc = spawn_cli_process(cmd=cmd, cwd=workdir, env=env)
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
        cli_projects.remove_turn_project(scratch_dir, env)
