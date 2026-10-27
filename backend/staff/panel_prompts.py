"""Panel stance parsing and prompt builders (split from ``staff.panel`` for the 500-line cap)."""

from __future__ import annotations

import re
from typing import Any

from staff.group_models import GroupCostEstimate, get_group_threshold, lookup_seat_price
from staff.panel_models import MODERATOR_NAME, STANCES, PanelCreateRequest, PanelSpeaker, default_quorum

TURN_EXCERPT_CHARS = 3000
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
    request: PanelCreateRequest,
    speaker: PanelSpeaker,
    round_no: int,
    turns: list[dict[str, Any]],
    codebase_context: str = "",
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
        f"{codebase_context}\n\n"
        f"## Discussion so far\n{_transcript(turns)}\n\n"
        "End your turn with exactly these two lines:\n"
        "STANCE: agree | partly | disagree  (with the emerging panel consensus)\n"
        "POSITION: <your current position in one sentence>"
    )


def compute_tally(seats: list[str], turns: list[dict[str, Any]], quorum: int | None) -> dict[str, Any]:
    """Participation tally for panel seats."""
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
