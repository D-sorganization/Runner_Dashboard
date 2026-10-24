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
    seat_label,
    seat_on_node,
)
from staff.groups import collate_consensus, estimate_group_turn_cost, execute_group_turn, get_group


@pytest.mark.unit
def test_seat_on_node_leaves_enabled_seat_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    """seat_on_node leaves an enabled seat identical (same object)."""
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "antigravity")
    alpha_seat = next(s for s in BOARD_SEATS if s.name == "alpha")
    result = seat_on_node(alpha_seat)
    assert result is alpha_seat


@pytest.mark.unit
def test_seat_on_node_disabled_antigravity_stands_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """An antigravity seat with antigravity disabled becomes provider claude, model sonnet-5, keeping other fields."""
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "antigravity")
    delta_seat = next(s for s in BOARD_SEATS if s.name == "delta")
    result = seat_on_node(delta_seat)

    assert result is not delta_seat
    assert result.provider == SEAT_STAND_IN_PROVIDER
    assert result.provider == "claude"
    assert result.model == SEAT_STAND_IN_MODEL
    assert result.model == "sonnet-5"
    assert result.name == delta_seat.name
    assert result.title == delta_seat.title
    assert result.role == delta_seat.role
    assert result.mandate == delta_seat.mandate


@pytest.mark.unit
def test_seat_on_node_unset_env_var_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the env var unset, the antigravity seat is unchanged (same object)."""
    monkeypatch.delenv("STAFF_DISABLED_PROVIDERS", raising=False)
    delta_seat = next(s for s in BOARD_SEATS if s.name == "delta")
    result = seat_on_node(delta_seat)
    assert result is delta_seat
    assert result.provider == "antigravity"


@pytest.mark.unit
def test_seat_on_node_antigravity_cli_spelling_stands_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """'Antigravity-CLI' spelling in the env var also stands the seat in (provider_switch canonicalises)."""
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "Antigravity-CLI")
    delta_seat = next(s for s in BOARD_SEATS if s.name == "delta")
    result = seat_on_node(delta_seat)

    assert result.provider == "claude"
    assert result.model == "sonnet-5"
    assert result.name == delta_seat.name
    assert result.title == delta_seat.title
    assert result.role == delta_seat.role
    assert result.mandate == delta_seat.mandate


@pytest.mark.unit
def test_estimate_group_turn_cost_with_disabled_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """estimate_group_turn_cost('board') with antigravity disabled prices Delta at claude/sonnet-5."""
    prompt = "Review the current architecture and metrics."
    prompt_tokens = max(100, int(len(prompt) / 4))
    estimated_output_tokens = 800

    expected_price = lookup_seat_price("claude", "sonnet-5")
    expected_delta_cost = expected_price.cost(prompt_tokens, estimated_output_tokens)

    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "antigravity")
    estimate = estimate_group_turn_cost("board", prompt=prompt)

    assert estimate.cost_per_seat["delta"] == expected_delta_cost
    assert set(estimate.cost_per_seat.keys()) == {"alpha", "bravo", "charlie", "delta"}
    assert len(estimate.cost_per_seat) == 4


@pytest.mark.asyncio
async def test_fanout_with_disabled_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fan-out runs all 4 seats; with antigravity disabled no recorded provider is 'antigravity'."""
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "antigravity")
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
    assert all(s.provider != "antigravity" for s in recorded_seats)

    seat_by_name = {s.name: s for s in recorded_seats}
    assert "delta" in seat_by_name
    assert seat_by_name["delta"].provider == "claude"
    assert seat_by_name["delta"].model == "sonnet-5"


@pytest.mark.unit
def test_seat_label_names_the_stand_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stood-in seat says so; Delta (Argon) runs on antigravity, so a silent swap would misattribute."""
    delta_seat = next(s for s in BOARD_SEATS if s.name == "delta")
    monkeypatch.delenv("STAFF_DISABLED_PROVIDERS", raising=False)
    assert seat_label(delta_seat) == delta_seat.title
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "antigravity")
    assert seat_label(delta_seat) == f"{delta_seat.title} (stand-in: claude — antigravity disabled on this node)"


@pytest.mark.unit
def test_board_summary_marks_the_stand_in_seat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both the positions line and the seat's reply header carry the stand-in marker."""
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "antigravity")
    replies = [SeatReply(seat_name=s.name, status="ok", text=f"{s.name} says yes") for s in BOARD_SEATS]
    res = collate_consensus(get_group("board"), "Adopt the plan?", replies)
    marker = "Delta (Docs & UX) - Argon (stand-in: claude — antigravity disabled on this node)"
    assert f"- **{marker}:**" in res.summary
    assert f"#### {marker}\n" in res.summary
    assert "Alpha (Architecture) - Fable (stand-in" not in res.summary
