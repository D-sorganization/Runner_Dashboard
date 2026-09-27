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
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from staff.chat_streaming import TurnStreamOutput
from staff.cli_projects import encode_project_name
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.panel import (
    PanelSpeaker,
    TurnOutcome,
    default_turn_runner,
    estimate_panel_cost,
    panel_result,
    parse_stance,
    run_panel,
    start_panel_thread,
)
from staff.panel_models import PANEL_PRESETS, PanelCreateRequest
from staff.thread_bus import get_thread_bus, reset_thread_bus

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


@pytest.mark.asyncio
async def test_default_turn_runner_message_id_none_publishes_no_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When message_id is None, default_turn_runner passes a null bus so no tokens stream."""
    mock_adapter = MagicMock()
    mock_adapter.chat_argv.return_value = ["dummy", "cmd"]
    mock_adapter.runtime_env.return_value = {}

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 0

    passed_bus: Any = None

    async def fake_stream_turn_output(
        reader: Any, adapter: Any, bus: Any, thread_id: str, placeholder_id: str
    ) -> TurnStreamOutput:
        nonlocal passed_bus
        passed_bus = bus
        return TurnStreamOutput(
            stdout_lines=["All good\n"],
            stderr_lines=[],
            returncode=0,
            json_lines=False,
        )

    monkeypatch.setattr("staff.panel.get_adapter", lambda _prov: mock_adapter)
    spawned: list[list[str]] = []

    def fake_spawn(**kw: Any) -> Any:
        spawned.append(list(kw["cmd"]))
        return mock_proc

    monkeypatch.setattr("staff.panel.spawn_cli_process", fake_spawn)
    monkeypatch.setattr("staff.panel.stream_turn_output", fake_stream_turn_output)

    speaker = PanelSpeaker(name="Ada", perspective="numerical methods", provider="claude", model="opus-5")
    real_bus = get_thread_bus()
    real_bus_mock = AsyncMock()
    monkeypatch.setattr(real_bus, "publish_token", real_bus_mock)

    outcome = await default_turn_runner(speaker, "prompt", "thread-1", None)
    assert outcome.ok is True
    assert outcome.text == "All good"
    assert passed_bus is not real_bus
    # Calling publish_token on the passed null bus must not raise and does nothing
    await passed_bus.publish_token("thread-1", "", "token")
    real_bus_mock.assert_not_called()

    # When message_id is provided, real bus is passed
    await default_turn_runner(speaker, "prompt", "thread-1", "msg-123")
    assert passed_bus is real_bus
    # The silent path spawns exactly the same CLI argv as the streaming panel path.
    assert len(spawned) == 2 and spawned[0] == spawned[1]


@pytest.mark.asyncio
async def test_default_turn_runner_refuses_a_disabled_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """STAFF_DISABLED_PROVIDERS (#1597) holds on panel and Board seat turns too: no CLI is spawned."""
    spawned: list[Any] = []
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "gemini")
    monkeypatch.setattr("staff.panel.spawn_cli_process", lambda **kw: spawned.append(kw))

    speaker = PanelSpeaker(name="Gem", perspective="p", provider="gemini")
    outcome = await default_turn_runner(speaker, "prompt", "thread-1", None)

    assert outcome.ok is False
    assert "STAFF_DISABLED_PROVIDERS" in (outcome.error or "")
    assert spawned == []


@pytest.mark.asyncio
async def test_default_turn_runner_removes_project_folder_on_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #1683: default_turn_runner removes <CLAUDE_CONFIG_DIR>/projects/<encoded cwd> on success and failure."""
    cfg_dir = tmp_path / "cfg"
    projects_dir = cfg_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    fake_temp = tmp_path / "tmp"
    fake_temp.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(fake_temp))

    mock_adapter = MagicMock()
    mock_adapter.chat_argv.return_value = ["dummy", "cmd"]
    mock_adapter.runtime_env.return_value = {"CLAUDE_CONFIG_DIR": str(cfg_dir)}

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 0

    captured_cwds: list[str] = []

    def fake_spawn(**kw: Any) -> Any:
        cwd = kw["cwd"]
        captured_cwds.append(cwd)
        proj_dir = projects_dir / encode_project_name(cwd)
        proj_dir.mkdir(parents=True, exist_ok=True)
        return mock_proc

    turn_output = TurnStreamOutput(
        stdout_lines=["All good\n"],
        stderr_lines=[],
        returncode=0,
        json_lines=False,
    )

    async def fake_stream_turn_output(*_args: Any, **_kwargs: Any) -> TurnStreamOutput:
        return turn_output

    monkeypatch.setattr("staff.panel.get_adapter", lambda _prov: mock_adapter)
    monkeypatch.setattr("staff.panel.spawn_cli_process", fake_spawn)
    monkeypatch.setattr("staff.panel.stream_turn_output", fake_stream_turn_output)

    speaker = PanelSpeaker(name="Ada", perspective="numerical methods", provider="claude", model="opus-5")

    # Success turn
    outcome_ok = await default_turn_runner(speaker, "prompt", "thread-1", None)
    assert outcome_ok.ok is True
    assert len(captured_cwds) == 1
    first_scratch = captured_cwds[0]
    first_proj = projects_dir / encode_project_name(first_scratch)
    assert not first_proj.exists()
    assert not Path(first_scratch).exists()

    # Failing turn
    turn_output = TurnStreamOutput(
        stdout_lines=[],
        stderr_lines=["turn crashed\n"],
        returncode=1,
        json_lines=False,
    )
    outcome_fail = await default_turn_runner(speaker, "prompt", "thread-1", None)
    assert outcome_fail.ok is False
    assert len(captured_cwds) == 2
    second_scratch = captured_cwds[1]
    second_proj = projects_dir / encode_project_name(second_scratch)
    assert not second_proj.exists()
    assert not Path(second_scratch).exists()


@pytest.mark.unit
def test_run_panel_sweeps_stale_panel_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_panel invokes sweep_stale_panel_projects before the first round and tolerates sweep failures."""
    swept: list[dict[str, str]] = []

    def fake_sweep(env: Any) -> list[str]:
        swept.append(dict(env))
        return []

    monkeypatch.setattr("staff.panel.cli_projects.sweep_stale_panel_projects", fake_sweep)
    runner = ScriptedRunner()
    _run(_request(rounds=1), runner)
    assert len(swept) == 1

    # Verify that an exception during sweep is logged and does not fail the panel
    def exploding_sweep(_env: Any) -> list[str]:
        raise RuntimeError("simulated disk error during sweep")

    monkeypatch.setattr("staff.panel.cli_projects.sweep_stale_panel_projects", exploding_sweep)
    thread_id = _run(_request(rounds=1), runner)
    res = panel_result(get_conversation_store(), thread_id)
    assert res is not None and res["status"] == "complete"
