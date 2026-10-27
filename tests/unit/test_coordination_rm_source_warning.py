"""Coordination sessions flag a frozen RM checkout instead of reporting an empty board as healthy (#2978)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from coordination import service

pytestmark = pytest.mark.unit

EMPTY_BOARD = {"available": True, "complete": True, "sessions": [], "messages": [], "conflicts": [], "warnings": []}


@pytest.fixture
def rm_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "rm"
    root.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("STAFF_RM_ROOT", str(root))
    monkeypatch.setenv("RUNNER_DASHBOARD_CONFIG_DIR", str(config))
    monkeypatch.setattr(service.board, "read_sessions", lambda repo=None: dict(EMPTY_BOARD))

    async def no_staff(repo=None):
        return ([], [])

    monkeypatch.setattr(service, "staff_runs", no_staff)

    def write(**state) -> None:
        (config / "rm_source_status.json").write_text(json.dumps({"root": str(root.resolve()), **state}))

    return write


def test_repeated_skips_add_a_board_warning(rm_state) -> None:
    rm_state(status="skipped", reason="checkout has local changes", consecutive_skips=5, attention=True)

    result = asyncio.run(service.sessions(None))

    warnings = [w for w in result["warnings"] if w.startswith("RM checkout")]
    assert len(warnings) == 1
    assert "checkout has local changes" in warnings[0]
    assert "stale" in warnings[0]


def test_failed_refresh_adds_a_board_warning(rm_state) -> None:
    rm_state(status="error", reason="CalledProcessError", consecutive_skips=0, attention=False)

    result = asyncio.run(service.sessions(None))

    assert any(w.startswith("RM checkout") and "CalledProcessError" in w for w in result["warnings"])


@pytest.mark.parametrize(
    "state",
    [
        {"status": "unchanged", "consecutive_skips": 0, "attention": False},
        {"status": "skipped", "reason": "checkout has local changes", "consecutive_skips": 1, "attention": False},
    ],
)
def test_healthy_or_single_skip_adds_no_warning(rm_state, state) -> None:
    rm_state(**state)

    result = asyncio.run(service.sessions(None))

    assert not any(w.startswith("RM checkout") for w in result["warnings"])


def test_status_from_another_checkout_is_ignored(rm_state, tmp_path: Path) -> None:
    config = tmp_path / "config"
    stale = {"root": str(tmp_path / "other"), "status": "skipped", "reason": "x", "attention": True}
    (config / "rm_source_status.json").write_text(json.dumps(stale))

    result = asyncio.run(service.sessions(None))

    assert not any(w.startswith("RM checkout") for w in result["warnings"])
