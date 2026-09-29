"""The open Board proposal queue as Board seat context (Runner_Dashboard#1787).

The 2026-09-29 Board sessions showed why each proposal's own text must reach the seats:
given only a summary table, seats guessed item content and numbered items wrongly.
"""

from __future__ import annotations

import pytest
from proposals.models import ProposalItem
from staff.board_queue import render_queue_block


def _item(number: int, title: str, **kw: object) -> ProposalItem:
    base: dict[str, object] = {
        "number": number,
        "title": title,
        "target_repos": ["Runner_Dashboard"],
        "problem": f"Problem of {number}.",
        "evidence": f"Evidence of {number}.",
        "options_considered": "A or B.",
        "lean": "A.",
        "estimated_cost": "Low",
        "urgency": "Routine",
        "html_url": f"https://github.com/D-sorganization/Repository_Management/issues/{number}",
    }
    base.update(kw)
    return ProposalItem(**base)  # type: ignore[arg-type]


@pytest.mark.unit
def test_every_proposal_is_given_with_its_own_text_and_linked_pr() -> None:
    block = render_queue_block(
        [
            _item(1848, "BR-01 Make replay atomic", pull_request="D-sorganization/Runner_Dashboard#1776"),
            _item(1859, "BR-12 Keep mobile Inbox above the nav"),
        ]
    )
    assert "2 open proposals" in block
    for n in (1848, 1859):
        assert f"Proposal #{n}" in block
        assert f"Problem of {n}." in block
        assert f"Evidence of {n}." in block
    assert "Pull request: D-sorganization/Runner_Dashboard#1776" in block
    # Seats answer per proposal, keyed by the proposal number.
    assert "#N — accept | defer | needs-info | reject" in block


@pytest.mark.unit
def test_proposals_that_do_not_fit_are_named_not_silently_dropped() -> None:
    items = [_item(n, f"Proposal {n}", problem="x" * 3000) for n in range(1, 11)]
    block = render_queue_block(items, budget=8000)
    assert len(block) <= 8000
    assert "Proposal #1" in block
    assert "Not shown for length" in block
    assert "#10" in block.split("Not shown for length", 1)[1]


@pytest.mark.unit
def test_a_long_proposal_is_cut_with_a_link_to_its_full_text() -> None:
    block = render_queue_block([_item(7, "Huge", evidence="e" * 20000)])
    assert "(cut for length; full text: https://github.com/D-sorganization/Repository_Management/issues/7)" in block


@pytest.mark.unit
def test_an_empty_queue_says_so() -> None:
    assert "no open Board proposals" in render_queue_block([])
