"""The open Board proposal queue as Board seat context (Runner_Dashboard#1787).

``board.convene`` with ``include_queue`` hands every seat each open ``board:proposal``
item with its own text. Seats given only a summary table guess what an item says and
number items wrongly (the 2026-09-29 sessions), so nothing here is summarised: an item
is cut only at a stated limit with a link to its full text, and items that do not fit
are named at the end instead of being dropped.
"""

from __future__ import annotations

from collections.abc import Sequence

from proposals.models import ProposalItem

QUEUE_BLOCK_CHARS: int = 60000
PER_PROPOSAL_CHARS: int = 6000

DEFAULT_QUEUE_QUESTION = (
    "Review the open Board proposal queue. Decide each proposal from its own text and "
    "give one disposition per proposal."
)

_HEADER = """## Open Board proposal queue ({count} open proposals)

Decide each proposal from its own text below. Answer one line per proposal:
`#N — accept | defer | needs-info | reject — one-sentence reason`, keyed by the
proposal number. Then give a priority order for the accepted ones. Say needs-info
for any proposal whose text is not shown here rather than guessing what it says.
"""


class QueueUnavailableError(RuntimeError):
    """The open proposal queue could not be read, so the Board is not convened on it."""


def _render_item(item: ProposalItem) -> str:
    facts = [
        f"Target: {', '.join(item.target_repos) or 'unspecified'}",
        f"Effort: {item.estimated_cost or 'unspecified'}",
        f"Urgency: {item.urgency or 'unspecified'}",
    ]
    if item.pull_request:
        facts.append(f"Pull request: {item.pull_request}")
    if item.code_request_url:
        facts.append(f"Linked: {item.code_request_url}")
    parts = [f"### Proposal #{item.number} — {item.title}", " · ".join(facts)]
    for label, text in (
        ("Problem", item.problem),
        ("Evidence", item.evidence),
        ("Options considered", item.options_considered),
        ("Submitter's lean", item.lean),
    ):
        if text:
            parts.append(f"**{label}:** {text}")
    rendered = "\n".join(parts) + "\n"
    if len(rendered) > PER_PROPOSAL_CHARS:
        note = f"… (cut for length; full text: {item.html_url or f'proposal #{item.number}'})\n"
        rendered = rendered[: PER_PROPOSAL_CHARS - len(note)].rstrip() + " " + note
    return rendered


def render_queue_block(items: Sequence[ProposalItem], budget: int = QUEUE_BLOCK_CHARS) -> str:
    """Render ``items`` as one seat-context block of at most ``budget`` characters.

    Post: every proposal is either shown with its text or named in the closing
    "Not shown for length" line; none is silently omitted.
    """
    if not items:
        return "## Open Board proposal queue\n\nThere are no open Board proposals.\n"
    header = _HEADER.format(count=len(items))
    shown: list[str] = []
    omitted: list[ProposalItem] = []
    # Reserve room for the omitted-items line, sized for the worst case.
    reserve = 40 + sum(len(f" #{i.number}") for i in items)
    used = len(header) + reserve
    for item in items:
        rendered = _render_item(item)
        if not omitted and used + len(rendered) + 1 <= budget:
            shown.append(rendered)
            used += len(rendered) + 1
        else:
            omitted.append(item)
    block = header + "\n" + "\n".join(shown)
    if omitted:
        block += "\nNot shown for length (answer needs-info):" + "".join(f" #{i.number}" for i in omitted) + "\n"
    return block


async def fetch_queue_block() -> str:
    """Read the open proposals from GitHub and render them.

    Raises :class:`QueueUnavailableError` when the read fails, never an empty queue.
    """
    from proposals import service  # noqa: PLC0415 — keeps staff import-light

    try:
        items = await service.list_proposals(state="open")
    except Exception as exc:  # noqa: BLE001 — any read failure means no queue to show
        raise QueueUnavailableError(f"could not read the open proposal queue: {exc}") from exc
    return render_queue_block(items)
