"""Dashboard root resolution for agent-bus hook bridge (#2982)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard_root import resolve_dashboard_root


@pytest.mark.unit
def test_resolve_from_repo_root_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "backend" / "agent_bus").mkdir(parents=True)
    (tmp_path / "clients" / "fleet").mkdir(parents=True)
    monkeypatch.setenv("RUNNER_DASHBOARD_REPO_ROOT", str(tmp_path))
    assert resolve_dashboard_root() == tmp_path


@pytest.mark.unit
def test_resolve_from_script_location() -> None:
    root = resolve_dashboard_root()
    assert root is not None
    assert (root / "scripts" / "agent-bus-hook-bridge.py").is_file()
