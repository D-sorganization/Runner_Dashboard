"""Unit tests for Board seat stand-in on disabled providers (#1646).

A Board seat whose provider is switched off on this node (via STAFF_DISABLED_PROVIDERS)
must stand in on claude (SEAT_STAND_IN_PROVIDER / SEAT_STAND_IN_MODEL) instead of
failing every turn.
"""

from __future__ import annotations

import pytest
from staff.group_models import (
    BOARD_SEATS,
    SEAT_STAND_IN_MODEL,
    SEAT_STAND_IN_PROVIDER,
    SeatReply,
    SeatSpec,
    lookup_seat_price,
    seat_on_node,
)
from staff.groups import estimate_group_turn_cost, execute_group_turn


@pytest.mark.unit
def test_seat_on_node_leaves_enabled_seat_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    """seat_on_node leaves an enabled seat identical (same object)."""
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "gemini")
    alpha_seat = next(s for s in BOARD_SEATS if s.name == "alpha")
    result = seat_on_node(alpha_seat)
    assert result is alpha_seat


@pytest.mark.unit
def test_seat_on_node_disabled_gemini_stands_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """A gemini seat with gemini disabled becomes provider claude, model sonnet-5, keeping other fields."""
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "gemini")
    bravo_seat = next(s for s in BOARD_SEATS if s.name == "bravo")
    result = seat_on_node(bravo_seat)

    assert result is not bravo_seat
    assert result.provider == SEAT_STAND_IN_PROVIDER
    assert result.provider == "claude"
    assert result.model == SEAT_STAND_IN_MODEL
    assert result.model == "sonnet-5"
    assert result.name == bravo_seat.name
    assert result.title == bravo_seat.title
    assert result.role == bravo_seat.role
    assert result.mandate == bravo_seat.mandate


@pytest.mark.unit
def test_seat_on_node_unset_env_var_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the env var unset, the gemini seat is unchanged (same object)."""
    monkeypatch.delenv("STAFF_DISABLED_PROVIDERS", raising=False)
    bravo_seat = next(s for s in BOARD_SEATS if s.name == "bravo")
    result = seat_on_node(bravo_seat)
    assert result is bravo_seat
    assert result.provider == "gemini"


@pytest.mark.unit
def test_seat_on_node_gemini_cli_spelling_stands_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """'Gemini-CLI' spelling in the env var also stands the seat in (provider_switch canonicalises)."""
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "Gemini-CLI")
    bravo_seat = next(s for s in BOARD_SEATS if s.name == "bravo")
    result = seat_on_node(bravo_seat)

    assert result.provider == "claude"
    assert result.model == "sonnet-5"
    assert result.name == bravo_seat.name
    assert result.title == bravo_seat.title
    assert result.role == bravo_seat.role
    assert result.mandate == bravo_seat.mandate


@pytest.mark.unit
def test_estimate_group_turn_cost_with_disabled_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """estimate_group_turn_cost('board') with gemini disabled prices Bravo at claude/sonnet-5."""
    prompt = "Review the current architecture and metrics."
    prompt_tokens = max(100, int(len(prompt) / 4))
    estimated_output_tokens = 800

    expected_price = lookup_seat_price("claude", "sonnet-5")
    expected_bravo_cost = expected_price.cost(prompt_tokens, estimated_output_tokens)

    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "gemini")
    estimate = estimate_group_turn_cost("board", prompt=prompt)

    assert estimate.cost_per_seat["bravo"] == expected_bravo_cost
    assert set(estimate.cost_per_seat.keys()) == {"alpha", "bravo", "charlie", "delta"}
    assert len(estimate.cost_per_seat) == 4


@pytest.mark.asyncio
async def test_fanout_with_disabled_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fan-out runs all 4 seats; with gemini disabled no recorded provider is 'gemini'."""
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "gemini")
    recorded_seats: list[SeatSpec] = []

    async def fake_runner(seat: SeatSpec, prompt: str, thread_id: str) -> SeatReply:
        recorded_seats.append(seat)
        return SeatReply(
            seat_name=seat.name,
            status="ok",
            text=f"Deliberation reply from {seat.title}",
            cost_usd=0.01,
        )

    res = await execute_group_turn(
        group_id="board",
        prompt="Deliberation question",
        thread_id="th_stand_in_test",
        seat_runner=fake_runner,
    )

    assert len(recorded_seats) == 4
    assert len(res.seat_replies) == 4
    assert all(s.provider != "gemini" for s in recorded_seats)

    seat_by_name = {s.name: s for s in recorded_seats}
    assert "bravo" in seat_by_name
    assert seat_by_name["bravo"].provider == "claude"
    assert seat_by_name["bravo"].model == "sonnet-5"
