"""Seat fallback chains for board and panel (#3528)."""

from __future__ import annotations

import pytest
from staff.group_models import BOARD_SEATS
from staff.panel_models import PanelSpeaker
from staff.seat_fallback import FABLE_FALLBACK, run_turn_with_fallback, steps_for_board_seat, steps_for_speaker


@pytest.mark.asyncio
async def test_fable_chain_order() -> None:
    speaker = PanelSpeaker(name="Fable", perspective="skeptic", provider="claude", model="claude-fable-5-2")
    steps = steps_for_speaker(speaker)
    assert [(s.provider, s.model) for s in steps][:5] == [
        ("claude", "claude-fable-5-2"),
        ("cursor-agent", "claude-fable-5-2"),
        ("claude", "claude-opus-5-5"),
        ("codex", "gpt-6.2-sol"),
        ("antigravity", "gemini-4-carbon"),
    ]


@pytest.mark.asyncio
async def test_run_turn_with_fallback_degrades_on_first_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    speaker = PanelSpeaker(name="Fable", perspective="x", provider="claude", model="claude-fable-5-2")
    calls: list[tuple[str, str | None]] = []

    async def fake_runner(sp: PanelSpeaker, _prompt: str, _tid: str, _mid: str | None) -> object:
        calls.append((sp.provider, sp.model))
        if (sp.provider, sp.model) in {
            ("claude", "claude-fable-5-2"),
            ("cursor-agent", "claude-fable-5-2"),
        }:
            from staff.panel_models import TurnOutcome

            return TurnOutcome(ok=False, error="quota")
        from staff.panel_models import TurnOutcome

        return TurnOutcome(ok=True, text="ok")

    monkeypatch.setattr(
        "staff.seat_fallback._usable",
        lambda p: p in {"claude", "cursor-agent", "codex", "antigravity"},
    )
    outcome = await run_turn_with_fallback(speaker, "hi", "t1", None, fake_runner)
    assert outcome.ok is True
    assert "degraded" in outcome.text
    assert calls[0] == ("claude", "claude-fable-5-2")
    assert ("claude", "claude-opus-5-5") in calls
    assert "claude-opus-5-5" in outcome.text


def test_board_fable_seat_includes_cursor_before_claude_opus() -> None:
    fable = next(s for s in BOARD_SEATS if s.name == "alpha")
    steps = steps_for_board_seat(fable)
    # Alpha/Fable: own Claude Fable seat, then Cursor Fable, then Claude Opus — never agy Opus (#4494).
    assert [(s.provider, s.model) for s in steps] == [
        ("claude", "claude-fable-5-2"),
        ("cursor-agent", "claude-fable-5-2"),
        ("claude", "claude-opus-5-5"),
        ("codex", "gpt-6.2-sol"),
        ("antigravity", "gemini-4-carbon"),
        ("antigravity", "gemini-4-hydrogen"),
    ]
    assert FABLE_FALLBACK[1] == ("claude", "claude-opus-5-5")
