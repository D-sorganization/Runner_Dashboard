"""Unit tests for staff group seat runner (#1637).

Tests:
a) run_seat with a fake turn_runner returning TurnOutcome(ok=True, text="Position: ship it\\nbecause...")
   returns status ok, that exact text, and cost_usd > 0; the fake receives message_id None,
   speaker.provider == seat.provider, and a prompt containing the seat's mandate and the question.
b) a failing fake (ok=False, error="exit 1: boom") -> status "error", error_detail "exit 1: boom",
   text containing "boom".
c) seat_prompt is pure and contains title, mandate and question; seat_cost_usd grows with reply length.
d) regression: the canned strings "perspective: Analyzed prompt" and "concurs with general recommendation"
   appear nowhere in backend/staff.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from staff.group_models import SeatSpec
from staff.group_seat_runner import run_seat, seat_cost_usd, seat_prompt
from staff.panel_models import PanelSpeaker, TurnOutcome


@pytest.fixture
def sample_seat() -> SeatSpec:
    return SeatSpec(
        name="alpha",
        title="Alpha (Architecture)",
        role="board-seat-alpha",
        provider="claude",
        model="opus-5",
        mandate="Cross-repo architectural review",
    )


@pytest.mark.asyncio
async def test_run_seat_success(sample_seat: SeatSpec) -> None:
    """run_seat passes mandate/question, provider, and None message_id; returns ok status and text."""
    received: dict[str, Any] = {}

    async def fake_turn_runner(
        speaker: PanelSpeaker, prompt: str, thread_id: str, message_id: str | None
    ) -> TurnOutcome:
        received["speaker"] = speaker
        received["prompt"] = prompt
        received["thread_id"] = thread_id
        received["message_id"] = message_id
        return TurnOutcome(ok=True, text="Position: ship it\nbecause...")

    reply = await run_seat(
        sample_seat,
        "Should we ship version 2?",
        "thread-123",
        turn_runner=fake_turn_runner,
    )

    assert reply.seat_name == sample_seat.name
    assert reply.status == "ok"
    assert reply.text == "Position: ship it\nbecause..."
    assert reply.cost_usd > 0

    assert received["message_id"] is None
    assert received["speaker"].provider == sample_seat.provider
    assert sample_seat.mandate in received["prompt"]
    assert "Should we ship version 2?" in received["prompt"]


@pytest.mark.asyncio
async def test_run_seat_failure(sample_seat: SeatSpec) -> None:
    """Failing fake returns error status, error_detail, and text containing error message."""

    async def failing_turn_runner(
        speaker: PanelSpeaker, prompt: str, thread_id: str, message_id: str | None
    ) -> TurnOutcome:
        return TurnOutcome(ok=False, error="exit 1: boom")

    reply = await run_seat(
        sample_seat,
        "Should we ship version 2?",
        "thread-123",
        turn_runner=failing_turn_runner,
    )

    assert reply.seat_name == sample_seat.name
    assert reply.status == "error"
    assert reply.error_detail == "exit 1: boom"
    assert "boom" in reply.text


@pytest.mark.unit
def test_seat_prompt_pure_and_seat_cost_usd_grows(sample_seat: SeatSpec) -> None:
    """seat_prompt is pure and contains title/mandate/question; seat_cost_usd grows with reply length."""
    prompt_1 = seat_prompt(sample_seat, "How do we scale?")
    prompt_2 = seat_prompt(sample_seat, "How do we scale?")
    assert prompt_1 == prompt_2, "seat_prompt must be pure"

    assert sample_seat.title in prompt_1
    assert sample_seat.mandate in prompt_1
    assert "How do we scale?" in prompt_1

    cost_short = seat_cost_usd(sample_seat, prompt_1, "Short reply.")
    cost_long = seat_cost_usd(sample_seat, prompt_1, "Much longer reply with extensive justification " * 50)
    assert cost_short > 0
    assert cost_long > cost_short


@pytest.mark.unit
def test_regression_no_canned_stub_strings_in_backend_staff() -> None:
    """Regression: canned strings appear nowhere in backend/staff."""
    staff_dir = Path(__file__).resolve().parents[2] / "backend" / "staff"
    assert staff_dir.is_dir(), f"Expected directory {staff_dir}"

    canned_1 = "perspective: Analyzed prompt"
    canned_2 = "concurs with general recommendation"

    py_files = list(staff_dir.glob("*.py"))
    assert len(py_files) > 0

    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8")
        assert canned_1 not in content, f"Found canned string {canned_1!r} in {py_file}"
        assert canned_2 not in content, f"Found canned string {canned_2!r} in {py_file}"
