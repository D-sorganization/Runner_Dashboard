"""Fleet decision panel (#2511): model pass-through and Barb turnover on completion."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any
import pytest
from staff.adapters import get_adapter
from staff.barb_continuity import BarbContinuityStore
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.panel import (
    PanelSpeaker,
    TurnOutcome,
    panel_result,
    run_panel,
    start_panel_thread,
)
from staff.panel_models import PanelCreateRequest
from staff.thread_bus import reset_thread_bus

SEATS = [
    {"name": "Argon", "perspective": "efficiency", "provider": "antigravity", "model": "gemini-4-argon-high"},
    {"name": "Astra", "perspective": "architecture", "provider": "codex", "model": "gpt-6-astra"},
    {"name": "Fable", "perspective": "skeptic", "provider": "claude", "model": "claude-fable-5-1"},
]


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_runs.sqlite3"))
    monkeypatch.setenv("STAFF_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setenv("BARB_CONTINUITY_DB", str(tmp_path / "barb_default.sqlite3"))
    reset_conversation_store()
    reset_thread_bus()
    yield
    reset_conversation_store()
    reset_thread_bus()


def _request(**overrides: Any) -> PanelCreateRequest:
    data: dict[str, Any] = {"topic": "Adopt a shared BuildKit cache?", "experts": SEATS, "rounds": 1}
    data.update(overrides)
    return PanelCreateRequest.model_validate(data)


class _Runner:
    async def __call__(self, speaker: PanelSpeaker, prompt: str, thread_id: str, message_id: str) -> TurnOutcome:
        if speaker.name == "Moderator":
            return TurnOutcome(ok=True, text="**Consensus:** yes.")
        return TurnOutcome(ok=True, text=f"{speaker.name} ok\nSTANCE: agree\nPOSITION: yes")


@pytest.mark.unit
@pytest.mark.parametrize("seat", SEATS, ids=lambda s: s["name"])
def test_each_seat_model_reaches_its_cli_argv(seat: dict[str, str]) -> None:
    argv = get_adapter(seat["provider"]).chat_argv(prompt="q", workdir="/tmp/w", model=seat["model"])
    assert argv[argv.index("--model") + 1] == seat["model"]


@pytest.mark.unit
def test_completion_appends_a_barb_turnover_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = BarbContinuityStore(tmp_path / "barb.sqlite3")
    monkeypatch.setattr("staff.panel.get_barb_continuity_store", lambda: store)
    request = _request()
    thread = start_panel_thread(request, created_by="op", store=get_conversation_store())
    asyncio.run(run_panel(thread.id, request, runner=_Runner()))

    notes = store.list_turnover(scope="panel")
    assert len(notes) == 1
    assert "Adopt a shared BuildKit cache?" in notes[0]["summary"]
    assert "consensus reached" in notes[0]["summary"] and thread.id in notes[0]["summary"]


@pytest.mark.unit
def test_a_turnover_failure_never_fails_the_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Any:
        raise RuntimeError("db locked")

    monkeypatch.setattr("staff.panel.get_barb_continuity_store", boom)
    request = _request()
    thread = start_panel_thread(request, created_by="op", store=get_conversation_store())
    asyncio.run(run_panel(thread.id, request, runner=_Runner()))
    result = panel_result(get_conversation_store(), thread.id)
    assert result is not None and result["status"] == "complete"
