"""Board seats are the Argon / Astra / Fable / top-Grok personas (owner request 2026-10-23).

Each seat pins a CLI model id verified on hub-a and node-c with the node's
subscription CLI (``agy models``, ``codex`` models cache, ``cursor-agent --list-models``,
and a one-line ``-p`` turn per seat), so the seat actually runs that model instead of
the provider default. No pay-per-use API provider is used.
"""

from __future__ import annotations

import pytest
from staff.group_models import BOARD_SEATS, SEAT_STAND_IN_PROVIDER, SeatSpec, seat_on_node
from staff.group_seat_runner import run_seat
from staff.panel_models import PanelSpeaker, TurnOutcome

EXPECTED = {
    "alpha": ("Fable", "claude", "claude-fable-5-1"),
    "bravo": ("Astra", "codex", "gpt-6-astra"),
    "charlie": ("Grok", "cursor-agent", "grok-4.7-xhigh"),
    "delta": ("Argon", "antigravity", "gemini-4-argon-high"),
}


@pytest.mark.unit
def test_board_has_exactly_the_four_persona_seats() -> None:
    actual = {s.name: (s.provider, s.model) for s in BOARD_SEATS}
    assert actual == {name: (prov, model) for name, (_, prov, model) in EXPECTED.items()}


@pytest.mark.unit
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_seat_title_names_its_persona_and_pins_its_model(name: str) -> None:
    seat = next(s for s in BOARD_SEATS if s.name == name)
    assert EXPECTED[name][0] in seat.title
    assert seat.pin_model is True


@pytest.mark.asyncio
async def test_pinned_seat_runs_on_its_own_model() -> None:
    seen: list[PanelSpeaker] = []

    async def fake(speaker: PanelSpeaker, prompt: str, thread_id: str, message_id: str | None) -> TurnOutcome:
        seen.append(speaker)
        return TurnOutcome(ok=True, text="Position: ok")

    charlie = next(s for s in BOARD_SEATS if s.name == "charlie")
    await run_seat(charlie, "q", "th_x", turn_runner=fake)
    assert seen[0].provider == "cursor-agent"
    assert seen[0].model == "grok-4.7-xhigh"


@pytest.mark.unit
def test_stand_in_drops_the_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stood-in seat must not hand its own provider's model id to the stand-in CLI."""
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "cursor-agent")
    charlie = next(s for s in BOARD_SEATS if s.name == "charlie")
    stood_in = seat_on_node(charlie)
    assert stood_in.provider == SEAT_STAND_IN_PROVIDER
    assert stood_in.pin_model is False


@pytest.mark.unit
def test_unpinned_seat_defaults_off() -> None:
    seat = SeatSpec(name="x", title="X", role="r", provider="claude", model="opus-5")
    assert seat.pin_model is False
