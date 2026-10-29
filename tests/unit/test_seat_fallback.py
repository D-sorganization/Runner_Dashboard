"""Seat fallback chains for board and panel (#3528)."""

from __future__ import annotations

import pytest

from staff.group_models import BOARD_SEATS
from staff.panel_models import PanelSpeaker
from staff.seat_fallback import FABLE_FALLBACK, run_turn_with_fallback, steps_for_board_seat, steps_for_speaker


@pytest.mark.asyncio
async def test_fable_chain_order() -> None:
    speaker = PanelSpeaker(name="Fable", perspective="skeptic", provider="claude", model="claude-fable-5-1")
    steps = steps_for_speaker(speaker)
    assert [s.provider for s in steps][:4] == ["claude", "cursor-agent", "antigravity", "codex"]


@pytest.mark.asyncio
async def test_run_turn_with_fallback_degrades_on_first_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    speaker = PanelSpeaker(name="Fable", perspective="x", provider="claude", model="claude-fable-5-1")
    calls: list[str] = []

    async def fake_runner(sp: PanelSpeaker, _prompt: str, _tid: str, _mid: str | None) -> object:
        calls.append(sp.provider)
        if sp.provider in {"claude", "cursor-agent"}:
            from staff.panel_models import TurnOutcome

            return TurnOutcome(ok=False, error="quota")
        from staff.panel_models import TurnOutcome

        return TurnOutcome(ok=True, text="ok")

    monkeypatch.setattr(
        "staff.seat_fallback._usable",
        lambda p: p in {"claude", "cursor-agent", "antigravity", "codex"},
    )
    outcome = await run_turn_with_fallback(speaker, "hi", "t1", None, fake_runner)
    assert outcome.ok is True
    assert "degraded" in outcome.text
    assert calls[0] == "claude"
    assert "antigravity" in calls


def test_board_fable_seat_includes_cursor_before_agy() -> None:
    fable = next(s for s in BOARD_SEATS if s.name == "alpha")
    steps = steps_for_board_seat(fable)
    providers = [s.provider for s in steps]
    assert providers.index("cursor-agent") < providers.index("antigravity")
    assert FABLE_FALLBACK[1][0] == "antigravity"
