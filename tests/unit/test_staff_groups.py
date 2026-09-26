"""Unit tests for staff group threads and Board Deliberation (SC-B9, Issue #1339).

Tests:
1. Group definitions and loading (Board group with seats: Alpha, Bravo, Charlie, Delta, coordinator: board-secretary).
2. Cost estimation per seat and total cost calculation with threshold guard.
3. Group turn fanout to fake seats: all seats respond -> consensus summary + expandable seat views.
4. Partial failure: one seat raises error -> listed as "no response", summary reports responding seats.
5. Timeout handling: one seat times out -> listed as "no response", remaining seats collated.
6. Formal proposal generation: "make this a proposal" action proposal (board.propose) created and executable.
7. Cost guard check: trips when estimated cost exceeds threshold; passes when confirmed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from staff.conversations import (
    ConversationStore,
    get_conversation_store,
    reset_conversation_store,
)
from staff.groups import (
    SeatReply,
    SeatSpec,
    estimate_group_turn_cost,
    execute_group_turn,
    get_group,
    list_groups,
)
from staff.thread_bus import reset_thread_bus


@pytest.fixture
def conv_store(tmp_path: Path) -> ConversationStore:
    reset_conversation_store()
    reset_thread_bus()
    db_file = tmp_path / "test_groups.sqlite3"
    return get_conversation_store(db_file)


# ── 1. GROUP DEFINITIONS ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_board_group_definition() -> None:
    """Board group has board-secretary coordinator and 4 seats (Alpha, Bravo, Charlie, Delta)."""
    group = get_group("board")
    assert group is not None
    assert group.id == "board"
    assert group.coordinator == "board-secretary"
    seat_names = [s.name for s in group.seats]
    assert "alpha" in seat_names
    assert "bravo" in seat_names
    assert "charlie" in seat_names
    assert "delta" in seat_names
    assert len(group.seats) == 4


@pytest.mark.unit
def test_list_groups_contains_board() -> None:
    """list_groups returns all registered groups including board."""
    groups = list_groups()
    ids = [g.id for g in groups]
    assert "board" in ids


# ── 2. COST ESTIMATION & GUARD ───────────────────────────────────────────────


@pytest.mark.unit
def test_estimate_group_turn_cost() -> None:
    """estimate_group_turn_cost calculates per-seat and total estimated USD cost."""
    estimate = estimate_group_turn_cost("board", prompt="Should we migrate runner cache to S3?")
    assert estimate.group_id == "board"
    assert estimate.total_cost_usd > 0.0
    assert len(estimate.cost_per_seat) == 4
    assert all(c > 0.0 for c in estimate.cost_per_seat.values())
    assert isinstance(estimate.exceeds_threshold, bool)


@pytest.mark.unit
def test_cost_guard_threshold_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cost guard flags turns that exceed configured cost threshold."""
    # Force a very low threshold so it triggers
    monkeypatch.setenv("GROUP_COST_GUARD_THRESHOLD_USD", "0.0001")
    estimate = estimate_group_turn_cost("board", prompt="A regular prompt")
    assert estimate.exceeds_threshold is True
    assert estimate.warning is not None
    assert "exceeds threshold" in estimate.warning.lower()


# ── 3. FANOUT TO FAKE SEATS (ALL SUCCEED) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_group_turn_all_seats_succeed() -> None:
    """All 4 seats respond with thoughtful replies -> consensus summary with all seats."""

    async def fake_runner(seat: SeatSpec, prompt: str, thread_id: str) -> SeatReply:
        return SeatReply(
            seat_name=seat.name,
            status="ok",
            text=f"Seat {seat.name.title()} perspective on {prompt}: strongly in favor.",
            cost_usd=0.01,
        )

    res = await execute_group_turn(
        group_id="board",
        prompt="Adopt WebGPU for visualization",
        thread_id="th_test1",
        seat_runner=fake_runner,
    )

    assert res.group_id == "board"
    assert res.coordinator == "board-secretary"
    assert len(res.seat_replies) == 4
    assert all(r.status == "ok" for r in res.seat_replies)
    assert "4/4 seats answered" in res.quorum
    assert "Where the seats stand" in res.summary
    assert "Alpha" in res.summary
    assert "Bravo" in res.summary
    assert "Charlie" in res.summary
    assert "Delta" in res.summary
    # Formal proposal recommended
    assert len(res.proposed_actions) >= 1
    assert res.proposed_actions[0].action == "board.propose"
    assert "Adopt WebGPU" in res.proposed_actions[0].params.get("title", "")


# ── 4. PARTIAL FAILURE ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_group_turn_partial_failure() -> None:
    """If one seat errors out, it is recorded as 'no response' and quorum is updated."""

    async def failing_runner(seat: SeatSpec, prompt: str, thread_id: str) -> SeatReply:
        if seat.name == "bravo":
            raise RuntimeError("API connection refused 502")
        return SeatReply(
            seat_name=seat.name,
            status="ok",
            text=f"Seat {seat.name.title()} recommends approval.",
            cost_usd=0.01,
        )

    res = await execute_group_turn(
        group_id="board",
        prompt="Review database compaction frequency",
        thread_id="th_test2",
        seat_runner=failing_runner,
    )

    assert len(res.seat_replies) == 4
    bravo_reply = next(r for r in res.seat_replies if r.seat_name == "bravo")
    assert bravo_reply.status == "error"
    assert "no response" in bravo_reply.text.lower()
    assert "3/4 seats answered" in res.quorum
    assert "Bravo: no response" in res.quorum


# ── 5. TIMEOUT HANDLING ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_group_turn_timeout() -> None:
    """If a seat exceeds seat_timeout_seconds, it is aborted and marked as timed out."""

    async def slow_runner(seat: SeatSpec, prompt: str, thread_id: str) -> SeatReply:
        if seat.name == "charlie":
            await asyncio.sleep(2.0)  # Will exceed our test timeout of 0.1s
            return SeatReply(seat_name=seat.name, status="ok", text="Late reply")
        return SeatReply(
            seat_name=seat.name,
            status="ok",
            text=f"Seat {seat.name.title()} reply on time.",
            cost_usd=0.01,
        )

    res = await execute_group_turn(
        group_id="board",
        prompt="Evaluate runner autoscaling",
        thread_id="th_test3",
        seat_runner=slow_runner,
        seat_timeout_seconds=0.1,
    )

    charlie_reply = next(r for r in res.seat_replies if r.seat_name == "charlie")
    assert charlie_reply.status == "timeout"
    assert "no response - timed out" in charlie_reply.text.lower()
    assert "Charlie: no response" in res.quorum
