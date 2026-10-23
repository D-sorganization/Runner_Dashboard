"""Panel hardening (#2545): seat model/provider validation and quorum/abstention semantics.

- A seat's model id can never smuggle argv flags into ``chat_argv``; the 422 names the seat.
- A seat with no successful turn is ``abstained`` (with a reason), the tally and ``quorum_met`` are
  exposed and stored, and the synthesis prompt carries the tally and says it does not override it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from staff.adapters import get_adapter
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.panel import PanelSpeaker, TurnOutcome, compute_tally, panel_result, run_panel, start_panel_thread
from staff.panel_models import PanelCreateRequest
from staff.thread_bus import reset_thread_bus

EXPERTS = [
    {"name": "Ada", "perspective": "numerical methods"},
    {"name": "Brook", "perspective": "experimental physics"},
    {"name": "Cy", "perspective": "software architecture"},
]


@pytest.fixture(autouse=True)
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_runs.sqlite3"))
    monkeypatch.setenv("STAFF_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    reset_conversation_store()
    reset_thread_bus()
    yield get_conversation_store()
    reset_conversation_store()
    reset_thread_bus()


def _request(**overrides: Any) -> PanelCreateRequest:
    data: dict[str, Any] = {"topic": "Use implicit stepping?", "experts": EXPERTS, "rounds": 1}
    data.update(overrides)
    return PanelCreateRequest.model_validate(data)


# ── model id validation ─────────────────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize(
    "model",
    ["--dangerously-skip-permissions", "-x", "x; rm -rf /", "a b", "$(id)", "m`id`", "a" * 81, "", "x\nmodel", "é"],
)
def test_a_hostile_model_id_is_rejected_naming_the_seat(model: str) -> None:
    experts = [{**EXPERTS[0], "model": model}, *EXPERTS[1:]]
    with pytest.raises(ValidationError) as err:
        _request(experts=experts)
    assert "Ada" in str(err.value)


@pytest.mark.unit
@pytest.mark.parametrize(
    "model", ["claude-fable-5-1", "gpt-6-astra", "gemini-4-argon-high", "llama3.2:3b", "org/m_1.5"]
)
def test_ordinary_model_ids_are_accepted(model: str) -> None:
    assert _request(experts=[{**EXPERTS[0], "model": model}, *EXPERTS[1:]]).experts[0].model == model


@pytest.mark.unit
def test_a_hostile_moderator_model_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _request(moderator_model="--dangerously-skip-permissions")


@pytest.mark.unit
def test_an_unknown_provider_is_rejected_naming_the_seat() -> None:
    with pytest.raises(ValidationError) as err:
        _request(experts=[{**EXPERTS[0], "provider": "rm -rf"}, *EXPERTS[1:]])
    assert "Ada" in str(err.value)


@pytest.mark.unit
def test_a_validated_model_only_ever_appears_as_the_value_after_model_flag() -> None:
    """The reason the pattern matters: chat_argv appends the model verbatim after ``--model``."""
    cmd = get_adapter("claude").chat_argv(prompt="hi", workdir=".", model="claude-fable-5-1")
    assert cmd[cmd.index("--model") + 1] == "claude-fable-5-1"
    # an unvalidated hostile value would land in argv as its own element -- which validation prevents:
    hostile = get_adapter("claude").chat_argv(prompt="hi", workdir=".", model="--add-dir")
    assert "--add-dir" in hostile
    with pytest.raises(ValidationError):
        _request(experts=[{**EXPERTS[0], "model": "--dangerously-skip-permissions"}, *EXPERTS[1:]])


# ── quorum / abstention ─────────────────────────────────────────────────────
def _runner(failing: set[str]) -> Any:
    prompts: list[tuple[str, str]] = []

    async def run(speaker: PanelSpeaker, prompt: str, thread_id: str, message_id: str) -> TurnOutcome:
        prompts.append((speaker.name, prompt))
        if speaker.name in failing:
            return TurnOutcome(ok=False, error="cli exited 2")
        if speaker.name == "Moderator":
            return TurnOutcome(ok=True, text="**Consensus:** yes.")
        return TurnOutcome(ok=True, text=f"{speaker.name}.\nSTANCE: agree\nPOSITION: fine")

    run.prompts = prompts  # type: ignore[attr-defined]
    return run


def _go(request: PanelCreateRequest, runner: Any) -> dict[str, Any]:
    thread = start_panel_thread(request, created_by="op", store=get_conversation_store())
    asyncio.run(run_panel(thread.id, request, runner=runner, turn_timeout=5))
    result = panel_result(get_conversation_store(), thread.id)
    assert result is not None
    return result


@pytest.mark.unit
def test_a_failed_seat_is_an_abstention_with_a_reason_and_quorum_still_holds() -> None:
    runner = _runner({"Brook"})
    result = _go(_request(), runner)
    assert result["tally"] == {"participated": 2, "abstained": 1}
    assert result["abstentions"] == [{"expert": "Brook", "reason": "cli exited 2"}]
    assert result["quorum_met"] is True and result["quorum"] == 2
    synth = next(p for n, p in runner.prompts if n == "Moderator")
    assert "2 of 3" in synth and "Brook" in synth and "abstained" in synth
    assert "does not override" in synth


@pytest.mark.unit
def test_quorum_is_lost_when_a_majority_abstains() -> None:
    result = _go(_request(), _runner({"Ada", "Brook"}))
    assert result["tally"] == {"participated": 1, "abstained": 2}
    assert result["quorum_met"] is False
    assert result["status"] == "complete"  # still synthesised, but labelled


@pytest.mark.unit
def test_the_synthesis_prompt_flags_a_missing_quorum() -> None:
    runner = _runner({"Ada", "Brook"})
    _go(_request(), runner)
    synth = next(p for n, p in runner.prompts if n == "Moderator")
    assert "was NOT met" in synth


@pytest.mark.unit
def test_a_full_panel_has_no_abstentions_and_the_tally_is_stored_on_the_thread() -> None:
    result = _go(_request(), _runner(set()))
    assert result["tally"] == {"participated": 3, "abstained": 0} and result["abstentions"] == []
    thread = get_conversation_store().get_thread(result["thread_id"])
    assert thread is not None and thread.meta["panel"]["tally"] == {"participated": 3, "abstained": 0}
    assert thread.meta["panel"]["quorum_met"] is True


@pytest.mark.unit
def test_a_seat_that_spoke_once_has_participated_even_if_a_later_turn_failed() -> None:
    turns = [
        {"expert": "Ada", "status": "ok"},
        {"expert": "Ada", "status": "error", "error": "boom"},
        {"expert": "Brook", "status": "timeout", "error": "no reply within 5 s"},
    ]
    tally = compute_tally(["Ada", "Brook", "Cy"], turns, quorum=None)
    assert tally["tally"] == {"participated": 1, "abstained": 2}
    assert [a["expert"] for a in tally["abstentions"]] == ["Brook", "Cy"]
    assert tally["abstentions"][1]["reason"] == "no turn recorded"


@pytest.mark.unit
def test_an_explicit_quorum_is_honoured_and_bounded() -> None:
    assert _go(_request(quorum=3), _runner({"Brook"}))["quorum_met"] is False
    with pytest.raises(ValidationError):
        _request(quorum=4)
