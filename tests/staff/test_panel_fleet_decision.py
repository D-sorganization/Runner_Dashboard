"""Fleet decision panel (#2511): model pass-through, read-only repo grounding and Barb turnover on completion."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from staff import panel_grounding
from staff.adapters import get_adapter
from staff.barb_continuity import BarbContinuityStore
from staff.chat_streaming import TurnStreamOutput
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.panel import (
    PanelSpeaker,
    TurnOutcome,
    build_turn_prompt,
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


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "init")
    (path / "untracked.txt").write_text("secret\n", encoding="utf-8")
    return path


class _Runner:
    async def __call__(self, speaker: PanelSpeaker, prompt: str, thread_id: str, message_id: str) -> TurnOutcome:
        if speaker.name == "Moderator":
            return TurnOutcome(ok=True, text="**Consensus:** yes.")
        return TurnOutcome(ok=True, text=f"{speaker.name} ok\nSTANCE: agree\nPOSITION: yes")


# ── model pass-through ──────────────────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize("seat", SEATS, ids=lambda s: s["name"])
def test_each_seat_model_reaches_its_cli_argv(seat: dict[str, str]) -> None:
    argv = get_adapter(seat["provider"]).chat_argv(prompt="q", workdir="/tmp/w", model=seat["model"])
    assert argv[argv.index("--model") + 1] == seat["model"]


# ── grounding ───────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_snapshot_holds_tracked_files_of_main_only(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "Repo")
    snap = panel_grounding.build_snapshot({"Repo": repo})
    assert snap is not None
    try:
        assert (Path(snap) / "Repo" / "mod.py").read_text(encoding="utf-8").strip() == "VALUE = 1"
        assert not (Path(snap) / "Repo" / "untracked.txt").exists()
        assert not (Path(snap) / "Repo" / ".git").exists()
    finally:
        panel_grounding.remove_snapshot(snap)
    assert not Path(snap).exists()


@pytest.mark.unit
def test_snapshot_of_a_non_repo_is_none(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    assert panel_grounding.build_snapshot({"plain": tmp_path / "plain"}) is None


@pytest.mark.asyncio
async def test_seats_run_in_the_snapshot_with_read_tools_only_and_it_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path / "Runner_Dashboard")
    monkeypatch.setattr("staff.workspace.find_repo_checkout", lambda name: repo)
    proc = MagicMock()
    proc.poll.return_value = 0
    spawned: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    prompts: list[str] = []

    async def fake_stream(**kw: Any) -> TurnStreamOutput:
        return TurnStreamOutput(
            stdout_lines=["ok\nSTANCE: agree\nPOSITION: p\n"], stderr_lines=[], returncode=0, json_lines=False
        )

    adapter = MagicMock()
    adapter.runtime_env.return_value = {}

    def chat_argv(**kw: Any) -> list[str]:
        calls.append(kw)
        prompts.append(kw["prompt"])
        return ["cli"]

    adapter.chat_argv.side_effect = chat_argv
    monkeypatch.setattr("staff.panel.get_adapter", lambda _p: adapter)
    monkeypatch.setattr("staff.panel.spawn_cli_process", lambda **kw: spawned.append(kw) or proc)
    monkeypatch.setattr("staff.panel.stream_turn_output", fake_stream)
    monkeypatch.setattr("staff.panel.record_panel_turnover", lambda *a, **k: None)

    request = _request(repos=["Runner_Dashboard"])
    thread = start_panel_thread(request, created_by="op", store=get_conversation_store())
    await run_panel(thread.id, request)

    seat_calls, moderator = calls[:3], calls[3]
    snapshot = seat_calls[0]["workdir"]
    assert all(c["workdir"] == snapshot for c in seat_calls)
    for c in seat_calls:
        assert set(c["read_only_tools"]) == {"view_file", "search_code", "list_files"}
    assert [s["cwd"] for s in spawned[:3]] == [snapshot] * 3
    assert moderator["workdir"] != snapshot and not moderator["read_only_tools"]
    assert "Runner_Dashboard" in prompts[0] and "read-only snapshots" in prompts[0]
    assert not Path(snapshot).exists()


@pytest.mark.unit
def test_without_repos_the_prompt_forbids_tools() -> None:
    prompt = build_turn_prompt(_request(), PanelSpeaker("Argon", "p", "claude"), 1, [])
    assert "Do not use tools" in prompt


# ── turnover ────────────────────────────────────────────────────────────────
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
