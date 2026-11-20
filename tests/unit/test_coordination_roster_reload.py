"""Roster cache invalidation after RM sync (#4587)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from coordination import roster as roster_mod

from tests.api.coordination_fake_rm import FakeRM

pytestmark = pytest.mark.unit


@pytest.fixture
def rm_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRM:
    fake = FakeRM(tmp_path / "rm")
    monkeypatch.setenv("STAFF_RM_ROOT", str(fake.root))
    monkeypatch.setenv("STAFF_RM_PYTHON", sys.executable)
    roster_mod.reset_cache()
    yield fake
    roster_mod.reset_cache()


def _freeze_clock(monkeypatch: pytest.MonkeyPatch, start: float = 1000.0) -> list[float]:
    now = [start]
    monkeypatch.setattr(roster_mod, "_clock", lambda: now[0])
    return now


def test_roster_reload_picks_up_rm_change_without_process_restart(
    rm_tree: FakeRM, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = _freeze_clock(monkeypatch)
    first = roster_mod.agent_ids()
    assert "gemini" in first and "grok" not in first

    rm_tree.set_roster(("user", "claude", "codex", "grok"))
    assert "grok" not in roster_mod.agent_ids()

    now[0] += roster_mod.CHECK_INTERVAL + 1
    second = roster_mod.agent_ids()
    assert "grok" in second and "gemini" not in second


def test_roster_reload_error_keeps_previous_roster(rm_tree: FakeRM, monkeypatch: pytest.MonkeyPatch) -> None:
    now = _freeze_clock(monkeypatch)
    assert "gemini" in roster_mod.agent_ids()
    now[0] += roster_mod.CHECK_INTERVAL + 1
    rm_tree.set_roster(None)

    kept = roster_mod.agent_ids()
    assert "gemini" in kept


def test_roster_does_not_reread_rm_inside_check_interval(rm_tree: FakeRM, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_clock(monkeypatch)
    calls: list[int] = []
    original = roster_mod._read_rm

    def counting_read() -> tuple[str, ...] | None:
        calls.append(1)
        return original()

    monkeypatch.setattr(roster_mod, "_read_rm", counting_read)
    roster_mod.reset_cache()

    assert roster_mod.agent_ids()
    assert roster_mod.agent_ids()
    assert len(calls) == 1
