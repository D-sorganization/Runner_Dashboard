"""Board consensus reports what the seats said, never canned approval (#1540).

`collate_consensus` used to return the same "consensus leans toward approving"
sentence for every turn, including one where no seat answered, and attached a
`board.propose` action with fixed repos, urgency and cost.
"""

from __future__ import annotations

import pytest
from staff.groups import SeatReply, collate_consensus, get_group

PROMPT = "Adopt WebGPU for visualization"


def _board():
    group = get_group("board")
    assert group is not None
    return group


def _ok(seat: str, text: str) -> SeatReply:
    return SeatReply(seat_name=seat, status="ok", text=text, cost_usd=0.01)


def _silent(seat: str) -> SeatReply:
    return SeatReply(seat_name=seat, status="timeout", text="", error_detail="timed out")


@pytest.mark.unit
def test_disagreeing_seats_are_reported_without_an_approval_claim() -> None:
    replies = [
        _ok("alpha", "Approve: WebGPU unblocks the 3D views."),
        _ok("bravo", "Reject: Safari support is not there yet."),
        _silent("charlie"),
        _ok("delta", "Defer until the docs site ships."),
    ]
    res = collate_consensus(_board(), PROMPT, replies)

    assert "leans toward approving" not in res.summary
    for position in (
        "Approve: WebGPU unblocks the 3D views.",
        "Reject: Safari support is not there yet.",
        "Defer until the docs site ships.",
    ):
        assert position in res.summary
    assert "3/4 seats answered" in res.summary


@pytest.mark.unit
def test_no_answers_means_no_quorum_and_no_proposal() -> None:
    replies = [_silent(s.name) for s in _board().seats]
    res = collate_consensus(_board(), PROMPT, replies)

    assert "No quorum" in res.summary
    assert "leans toward approving" not in res.summary
    assert res.proposed_actions == []


@pytest.mark.unit
def test_proposal_carries_the_discussion_and_no_fixed_fields() -> None:
    replies = [_ok("alpha", "Approve with a Safari fallback."), _silent("bravo")]
    res = collate_consensus(_board(), PROMPT, replies)

    assert len(res.proposed_actions) == 1
    action = res.proposed_actions[0]
    assert action.action == "board.propose"
    assert "Adopt WebGPU" in action.params["title"]
    assert "Approve with a Safari fallback." in action.params["proposal"]
    assert PROMPT in action.params["proposal"]
    for fixed in ("target_repos", "urgency", "estimated_cost"):
        assert fixed not in action.params


@pytest.mark.unit
def test_long_positions_are_truncated_in_the_summary_but_kept_in_seat_views() -> None:
    long_text = "Approve. " + "detail " * 200
    res = collate_consensus(_board(), PROMPT, [_ok("alpha", long_text)])

    head = res.summary.split("<details>")[0]
    assert long_text not in head
    assert "…" in head
    assert long_text in res.summary
