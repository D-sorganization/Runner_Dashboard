"""Lane-prompt checkout skips providers marked down (#4564 slice 1)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _ROOT / "backend"
for _p in (str(_ROOT), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from coordination import lane_prompts  # noqa: E402
from coordination import provider_down_markers as pdm  # noqa: E402


def _store(tmp_path: Path) -> lane_prompts.LanePromptStore:
    store = lane_prompts.LanePromptStore(db_path=tmp_path / "lane.sqlite3")
    store.register_assignment(
        lane_id="codex-worker-1",
        role="worker",
        repos=["Runner_Dashboard"],
        release_sha="sha",
        package_hash="hash",
        subagent_model="gpt-6-luna",
        max_children=1,
    )
    store.register_assignment(
        lane_id="cursor-worker-1",
        role="worker",
        repos=["Runner_Dashboard"],
        release_sha="sha",
        package_hash="hash",
        subagent_model="composer-2.5",
        max_children=1,
    )
    return store


def test_checkout_failover_from_codex_to_cursor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path / "state"))
    now = 4_000_000.0
    pdm.set_marker("codex", "provider_down", until=now + 600, now=now)
    store = _store(tmp_path)
    receipt = store.checkout(
        session_id="codex-worker-99",
        principal="agent-codex",
        idempotency_key="idem-1",
        role="worker",
        now=now,
    )
    assert receipt["lane_id"] == "cursor-worker-1"


def test_checkout_skips_multiple_down_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_DASHBOARD_STATE_DIR", str(tmp_path / "state"))
    now = 4_000_010.0
    pdm.set_marker("codex", "rate_limited", until=now + 120, now=now)
    pdm.set_marker("claude", "provider_down", until=now + 120, now=now)
    store = _store(tmp_path)
    store.register_assignment(
        lane_id="claude-worker-1",
        role="worker",
        repos=["Runner_Dashboard"],
        release_sha="sha",
        package_hash="hash",
        subagent_model="claude-sonnet-5-5",
        max_children=1,
    )
    receipt = store.checkout(
        session_id="codex-session",
        principal="agent-codex",
        idempotency_key="idem-2",
        role="worker",
        now=now,
    )
    assert receipt["lane_id"] == "cursor-worker-1"
