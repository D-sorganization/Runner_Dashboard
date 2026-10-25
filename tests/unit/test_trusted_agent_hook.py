"""Trusted Claude Stop hook skips non-git panel scratch directories (#2899)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "scripts" / "trusted_agent_hook.py"


def _load_hook_module() -> object:
    assert HOOK.is_file(), "scripts/trusted_agent_hook.py must exist"
    spec = importlib.util.spec_from_file_location("trusted_agent_hook", HOOK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["trusted_agent_hook"] = module
    spec.loader.exec_module(module)
    return module


def _run_hook(cwd: Path, *, env: dict[str, str] | None = None, stdin: str = "{}") -> subprocess.CompletedProcess[str]:
    merged = {**dict(**{k: v for k, v in __import__("os").environ.items()}), **(env or {})}
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
def test_trusted_agent_hook_skips_panel_turn_env_without_running_checks(tmp_path: Path) -> None:
    scratch = tmp_path / "staff_panel_flag"
    scratch.mkdir()
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
def test_trusted_agent_hook_runs_agent_check_in_real_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    hook = _load_hook_module()
    repo = Path(__file__).resolve().parents[2]
    bridge = repo / "scripts" / "agent-check-hook-bridge.py"
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(hook.subprocess, "run", fake_run)
    monkeypatch.setattr(hook, "discover_fleet_repo_root", lambda _start=None: repo)
    monkeypatch.setattr(hook, "_is_git_worktree", lambda _cwd: True)
    monkeypatch.setattr(hook.os, "environ", {})
    monkeypatch.setattr(hook.sys.stdin, "read", lambda: '{"hook_event_name":"Stop"}')
    assert hook.main() == 0
    assert calls
    assert calls[0][-2:] == [str(bridge), "claude-stop"]
