"""Trusted Claude Stop hook skips non-git panel scratch directories (#2899)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from shared_scripts.panel_turn_scratch import is_verified_panel_scratch, write_panel_scratch_marker

HOOK = Path(__file__).resolve().parents[2] / "scripts" / "trusted_agent_hook.py"
REPO = Path(__file__).resolve().parents[2]


def _load_hook_module() -> object:
    assert HOOK.is_file(), "scripts/trusted_agent_hook.py must exist"
    spec = importlib.util.spec_from_file_location("trusted_agent_hook", HOOK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["trusted_agent_hook"] = module
    spec.loader.exec_module(module)
    return module


def _run_hook(cwd: Path, *, env: dict[str, str] | None = None, stdin: str = "{}") -> subprocess.CompletedProcess[str]:
    import os

    merged = {**dict(os.environ), **(env or {})}
    return subprocess.run(
        [sys.executable, str(HOOK)],
        cwd=cwd,
        env=merged,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.unit
def test_trusted_agent_hook_skips_when_cwd_is_not_a_git_worktree(tmp_path: Path) -> None:
    scratch = tmp_path / "staff_panel_no_git"
    scratch.mkdir()
    result = _run_hook(scratch)
    assert result.returncode == 0
    assert result.stdout == ""
    assert "worktree" not in result.stderr.lower()


@pytest.mark.unit
def test_trusted_agent_hook_skips_verified_panel_scratch_with_panel_turn_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    scratch = tmp_path / "staff_panel_flag"
    scratch.mkdir()
    subprocess.run(["git", "init"], cwd=scratch, capture_output=True, text=True, check=True)
    write_panel_scratch_marker(scratch, REPO)
    result = _run_hook(scratch, env={"STAFF_PANEL_TURN": "1"})
    assert result.returncode == 0


@pytest.mark.unit
def test_trusted_agent_hook_skips_throwaway_git_init_without_fleet_manifest(tmp_path: Path) -> None:
    scratch = tmp_path / "staff_panel_git_only"
    scratch.mkdir()
    init = subprocess.run(["git", "init"], cwd=scratch, capture_output=True, text=True, check=False)
    assert init.returncode == 0, init.stderr
    result = _run_hook(scratch)
    assert result.returncode == 0
    assert "Agent hook checks failed" not in result.stdout


@pytest.mark.unit
def test_is_verified_panel_scratch_rejects_arbitrary_tmp_path(tmp_path: Path) -> None:
    arbitrary = tmp_path / "not_a_panel"
    arbitrary.mkdir()
    assert is_verified_panel_scratch(arbitrary) is False


@pytest.mark.unit
def test_is_verified_panel_scratch_accepts_panel_runner_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    scratch = tmp_path / "staff_panel_abc123"
    scratch.mkdir()
    subprocess.run(["git", "init"], cwd=scratch, capture_output=True, text=True, check=True)
    write_panel_scratch_marker(scratch, REPO)
    assert is_verified_panel_scratch(scratch) is True


@pytest.mark.unit
def test_trusted_agent_hook_runs_agent_check_in_real_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    hook = _load_hook_module()
    bridge = REPO / "scripts" / "agent-check-hook-bridge.py"
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(hook.subprocess, "run", fake_run)
    monkeypatch.setattr(hook, "discover_fleet_repo_root", lambda _start=None: REPO)
    monkeypatch.setattr(hook, "_is_git_worktree", lambda _cwd: True)
    monkeypatch.setattr(hook, "_should_skip_agent_check", lambda _cwd: False)
    monkeypatch.setattr(hook.os, "environ", {})
    monkeypatch.setattr(hook.sys.stdin, "read", lambda: '{"hook_event_name":"Stop"}')
    assert hook.main() == 0
    assert calls
    assert calls[0][-2:] == [str(bridge), "claude-stop"]
