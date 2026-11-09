"""Engine enforcement of the fail-closed subscription-only panel policy (#4223).

- With ``require_verified_subscription``, a turn never substitutes another provider or model,
  even when the fallback chain would otherwise degrade it.
- A provider that fails its subscription check is never handed to the runner at all.
- Without the flag, nothing changes: the ordinary fallback chain still applies.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import staff.panel as panel
from staff.conversations import get_conversation_store, reset_conversation_store
from staff.panel import PanelSpeaker, TurnOutcome, panel_result, run_panel, start_panel_thread
from staff.panel_models import PanelCreateRequest
from staff.thread_bus import reset_thread_bus

EXPERTS = [
    {"name": "Ada", "perspective": "numerical methods", "provider": "claude"},
    {"name": "Brook", "perspective": "experimental physics", "provider": "codex"},
    {"name": "Cy", "perspective": "software architecture", "provider": "antigravity"},
]


@pytest.fixture(autouse=True)
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "staff_runs.sqlite3"))
    monkeypatch.setenv("STAFF_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.delenv("STAFF_DISABLED_PROVIDERS", raising=False)
    reset_conversation_store()
    reset_thread_bus()
    yield get_conversation_store()
    reset_conversation_store()
    reset_thread_bus()


def _request(**overrides: Any) -> PanelCreateRequest:
    data: dict[str, Any] = {"topic": "Use implicit stepping?", "experts": EXPERTS, "rounds": 1}
    data.update(overrides)
    return PanelCreateRequest.model_validate(data)


def _go(request: PanelCreateRequest, runner: Any) -> dict[str, Any]:
    thread = start_panel_thread(request, created_by="op", store=get_conversation_store())
    asyncio.run(run_panel(thread.id, request, runner=runner, turn_timeout=5))
    result = panel_result(get_conversation_store(), thread.id)
    assert result is not None
    return result


def _recording_runner(seen: list[PanelSpeaker]) -> Any:
    async def run(speaker: PanelSpeaker, prompt: str, thread_id: str, message_id: str) -> TurnOutcome:
        seen.append(speaker)
        if speaker.name == "Moderator":
            return TurnOutcome(ok=True, text="**Consensus:** yes.")
        return TurnOutcome(ok=True, text=f"{speaker.name}.\nSTANCE: agree\nPOSITION: fine")

    return run


def _ok(provider: str = "irrelevant") -> Any:
    from staff.panel_subscription_policy import SubscriptionCheck

    return SubscriptionCheck(provider=provider, ok=True, reason="")


@pytest.mark.unit
def test_strict_mode_never_substitutes_a_provider_even_on_fallback_chains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(panel, "check_provider_subscription", lambda _p: _ok())
    seen: list[PanelSpeaker] = []
    result = _go(_request(require_verified_subscription=True), _recording_runner(seen))
    assert result["status"] == "complete"
    by_name = {s.name: (s.provider, s.model) for s in seen}
    assert by_name["Ada"] == ("claude", None)
    assert by_name["Brook"] == ("codex", None)
    assert by_name["Cy"] == ("antigravity", None)
    assert by_name["Moderator"] == ("claude", None)


@pytest.mark.unit
def test_strict_mode_fails_a_seat_closed_without_ever_calling_the_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from staff.panel_subscription_policy import SubscriptionCheck

    def fake_check(provider: str) -> SubscriptionCheck:
        if provider == "codex":
            return SubscriptionCheck(provider, False, "no Codex auth.json")
        return SubscriptionCheck(provider, True, "")

    monkeypatch.setattr(panel, "check_provider_subscription", fake_check)
    seen: list[PanelSpeaker] = []
    result = _go(_request(require_verified_subscription=True), _recording_runner(seen))
    assert "Brook" not in {s.name for s in seen}
    brook_turn = next(t for t in result["turns"] if t["expert"] == "Brook")
    assert brook_turn["status"] == "error"
    assert "subscription policy" in brook_turn["error"] and "no Codex auth.json" in brook_turn["error"]
    assert result["abstentions"] == [{"expert": "Brook", "reason": brook_turn["error"]}]


@pytest.mark.unit
def test_without_the_flag_the_ordinary_fallback_chain_still_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy behaviour (no opt-in) is unchanged: a disabled seat provider still degrades."""
    request = _request(require_verified_subscription=False)
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "codex")
    seen: list[PanelSpeaker] = []
    result = _go(request, _recording_runner(seen))
    brook_turns = [s for s in seen if s.name == "Brook"]
    assert brook_turns and brook_turns[0].provider != "codex"
    assert result["status"] == "complete"
