"""Expert panels (#1634, epic #1633): experts take turns for N rounds, then a moderator writes the consensus.

- Turns run one at a time and every prompt carries the whole discussion so far.
- A round in which every expert says ``STANCE: agree`` ends a debate early; a brainstorm always runs its rounds.
- A failed or timed-out turn is recorded as that expert's missed turn and the panel continues.
- The request contract rejects bad panels before anything runs.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.panel import (
    PanelSpeaker,
    TurnOutcome,
    estimate_panel_cost,
    panel_result,
    parse_stance,
    run_panel,
    start_panel_thread,
)
from staff.panel_models import PANEL_PRESETS, PanelCreateRequest
from staff.thread_bus import reset_thread_bus

EXPERTS = [
    {"name": "Ada", "perspective": "numerical methods"},
    {"name": "Brook", "perspective": "experimental physics"},
    {"name": "Cy", "perspective": "software architecture"},
]


@pytest.fixture(autouse=True)
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_runs.sqlite3"))
    reset_conversation_store()
    reset_thread_bus()
    yield get_conversation_store()
    reset_conversation_store()
    reset_thread_bus()


def _request(**overrides: Any) -> PanelCreateRequest:
    data: dict[str, Any] = {"topic": "Should the solver use implicit time stepping?", "experts": EXPERTS, "rounds": 2}
    data.update(overrides)
    return PanelCreateRequest.model_validate(data)


class ScriptedRunner:
    """Answers each turn from a script keyed by (speaker, call index); records every prompt."""

    def __init__(self, stances: dict[str, list[str]] | None = None, fail: set[str] | None = None) -> None:
        self.stances = stances or {}
        self.fail = fail or set()
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, speaker: PanelSpeaker, prompt: str, thread_id: str, message_id: str) -> TurnOutcome:
        self.calls.append((speaker.name, prompt))
        if speaker.name in self.fail:
            raise RuntimeError("provider exploded")
        if speaker.name == "Moderator":
            return TurnOutcome(ok=True, text="**Consensus:** use implicit stepping.")
        turn = sum(1 for name, _ in self.calls if name == speaker.name) - 1
        script = self.stances.get(speaker.name, ["partly"])
        stance = script[min(turn, len(script) - 1)]
        return TurnOutcome(
            ok=True,
            text=f"{speaker.name} says point {turn}.\nSTANCE: {stance}\nPOSITION: {speaker.name} view {turn}",
        )


def _run(request: PanelCreateRequest, runner: Any, timeout: float = 5.0) -> str:
    thread = start_panel_thread(request, created_by="operator", store=get_conversation_store())
    asyncio.run(run_panel(thread.id, request, runner=runner, turn_timeout=timeout))
    return thread.id


# ── the rounds ──────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_experts_take_turns_in_order_and_each_prompt_carries_the_discussion() -> None:
    runner = ScriptedRunner()
    thread_id = _run(_request(), runner)

    speakers = [name for name, _ in runner.calls]
    assert speakers == ["Ada", "Brook", "Cy", "Ada", "Brook", "Cy", "Moderator"]
    brook_round_2 = runner.calls[4][1]
    assert "Ada says point 0." in brook_round_2 and "Cy says point 0." in brook_round_2
    assert "Ada says point 1." in brook_round_2  # Ada already spoke this round
    assert "round 2 of 2" in brook_round_2
    assert "experimental physics" in brook_round_2 and "STANCE:" in brook_round_2

    result = panel_result(get_conversation_store(), thread_id)
    assert result is not None
    assert (result["status"], result["rounds_used"], result["consensus"]) == ("complete", 2, False)
    assert [(t["round"], t["expert"], t["stance"]) for t in result["turns"]][:3] == [
        (1, "Ada", "partly"),
        (1, "Brook", "partly"),
        (1, "Cy", "partly"),
    ]
    assert result["synthesis"] == "**Consensus:** use implicit stepping."


@pytest.mark.unit
def test_a_unanimous_round_ends_a_debate_early() -> None:
    runner = ScriptedRunner({"Ada": ["agree"], "Brook": ["agree"], "Cy": ["agree"]})
    thread_id = _run(_request(rounds=4), runner)

    assert [name for name, _ in runner.calls] == ["Ada", "Brook", "Cy", "Moderator"]
    assert "reached consensus in round 1 of 4" in runner.calls[-1][1]
    result = panel_result(get_conversation_store(), thread_id)
    assert result is not None and (result["rounds_used"], result["consensus"]) == (1, True)


@pytest.mark.unit
def test_a_brainstorm_runs_every_round() -> None:
    runner = ScriptedRunner({"Ada": ["agree"], "Brook": ["agree"], "Cy": ["agree"]})
    _run(_request(rounds=2, mode="brainstorm"), runner)
    assert len(runner.calls) == 7
    assert "build on" in runner.calls[0][1].lower()


@pytest.mark.unit
def test_a_failing_expert_is_recorded_and_the_panel_continues() -> None:
    runner = ScriptedRunner({"Ada": ["agree"], "Cy": ["agree"]}, fail={"Brook"})
    thread_id = _run(_request(rounds=1), runner)

    result = panel_result(get_conversation_store(), thread_id)
    assert result is not None
    brook = next(t for t in result["turns"] if t["expert"] == "Brook")
    assert brook["status"] == "error" and brook["stance"] is None
    assert result["consensus"] is False  # a missed turn is not agreement
    assert result["synthesis"]


@pytest.mark.unit
def test_a_slow_turn_times_out_as_a_missed_turn() -> None:
    async def slow(speaker: PanelSpeaker, prompt: str, thread_id: str, message_id: str) -> TurnOutcome:
        if speaker.name == "Cy":
            await asyncio.sleep(5)
        return TurnOutcome(ok=True, text="ok\nSTANCE: agree\nPOSITION: fine")

    thread_id = _run(_request(rounds=1), slow, timeout=0.2)
    result = panel_result(get_conversation_store(), thread_id)
    assert result is not None
    assert next(t for t in result["turns"] if t["expert"] == "Cy")["status"] == "timeout"


@pytest.mark.unit
def test_a_failed_moderator_marks_the_panel_failed() -> None:
    runner = ScriptedRunner(fail={"Moderator"})
    thread_id = _run(_request(rounds=1), runner)
    result = panel_result(get_conversation_store(), thread_id)
    assert result is not None and result["status"] == "failed" and result["synthesis"] is None


# ── stance parsing ──────────────────────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("blah\nSTANCE: agree\nPOSITION: Implicit wins.", ("agree", "Implicit wins.")),
        ("**STANCE:** Partially\n**POSITION:** Depends on stiffness", ("partly", "Depends on stiffness")),
        ("stance: DISAGREE", ("disagree", None)),
        ("STANCE: agree\nlater\nSTANCE: disagree", ("disagree", None)),
        ("no markers at all", (None, None)),
        ("STANCE: maybe", (None, None)),
    ],
)
def test_parse_stance(text: str, expected: tuple[str | None, str | None]) -> None:
    assert parse_stance(text) == expected


# ── the request contract ────────────────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    [
        {"experts": EXPERTS[:2]},
        {"experts": [*EXPERTS, {"name": "Di", "perspective": "x"}, {"name": "Ed", "perspective": "y"}]},
        {"experts": [*EXPERTS[:2], {"name": "ada", "perspective": "dup"}]},
        {"experts": [*EXPERTS[:2], {"name": "Moderator", "perspective": "reserved"}]},
        {"experts": [*EXPERTS[:2], {"name": "Cy", "perspective": "p", "provider": "no-such-cli"}]},
        {"rounds": 0},
        {"rounds": 7},
        {"topic": "   "},
        {"mode": "shouting"},
        {"surprise": True},
    ],
)
def test_bad_panels_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _request(**overrides)


@pytest.mark.unit
def test_presets_are_valid_panels() -> None:
    assert PANEL_PRESETS
    for preset in PANEL_PRESETS:
        PanelCreateRequest.model_validate({"topic": "t", "experts": preset["experts"]})


@pytest.mark.unit
def test_cost_grows_with_rounds_and_experts() -> None:
    small = estimate_panel_cost(_request(rounds=1))
    large = estimate_panel_cost(
        _request(rounds=6, experts=[*EXPERTS, {"name": "Di", "perspective": "operations"}]),
    )
    assert 0 < small.total_cost_usd < large.total_cost_usd
    assert set(large.cost_per_seat) == {"Ada", "Brook", "Cy", "Di", "Moderator"}
