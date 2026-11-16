"""Regression tests for the fail-closed gitleaks pre-commit wrapper (#4451)."""

from __future__ import annotations

import os
import subprocess
from unittest import mock

from scripts import gitleaks_pre_commit as hook


def test_pre_commit_runs_protect_staged() -> None:
    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch.object(hook, "_staged_has_changes", return_value=True):
            with mock.patch.object(hook, "_run_gitleaks") as run:
                run.return_value = subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="INF no leaks found\n", stderr=""
                )
                assert hook.main([]) == 0
                run.assert_called_once()
                cmd = run.call_args[0][0]
                assert cmd[:3] == ["gitleaks", "protect", "--staged"]


def test_pre_push_runs_detect_on_commit_range() -> None:
    env = {
        "PRE_COMMIT_FROM_REF": "abc123",
        "PRE_COMMIT_TO_REF": "def456",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch.object(hook, "_run_gitleaks") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            assert hook.main([]) == 0
            cmd = run.call_args[0][0]
            assert cmd[:2] == ["gitleaks", "detect"]
            assert "--log-opts" in cmd
            assert "abc123..def456" in cmd


def test_fail_closed_when_gitleaks_git_errors_but_exits_zero() -> None:
    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch.object(hook, "_staged_has_changes", return_value=True):
            with mock.patch.object(hook, "_run_gitleaks") as run:
                run.return_value = subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="INF 0 commits scanned.\nINF no leaks found\n",
                    stderr="ERR [git] error: unknown option `staged'\n",
                )
                assert hook.main([]) == 1


def test_fail_closed_zero_commits_scanned_with_staged_changes() -> None:
    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch.object(hook, "_staged_has_changes", return_value=True):
            with mock.patch.object(hook, "_run_gitleaks") as run:
                run.return_value = subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="INF 0 commits scanned.\nINF no leaks found\n",
                    stderr="",
                )
                assert hook.main([]) == 1


def test_leak_exit_code_passthrough() -> None:
    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch.object(hook, "_staged_has_changes", return_value=True):
            with mock.patch.object(hook, "_run_gitleaks") as run:
                run.return_value = subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="leaks found: 1\n", stderr=""
                )
                assert hook.main([]) == 1


def test_hook_config_uses_wrapper_for_pre_push() -> None:
    """Pre-push stage must not call bare ``gitleaks protect --staged`` (#4451)."""
    from pathlib import Path

    import yaml

    config = yaml.safe_load((Path(__file__).resolve().parents[1] / ".pre-commit-config.yaml").read_text())
    hooks = [h for repo in config["repos"] for h in repo.get("hooks", []) if h["id"] == "gitleaks"]
    assert len(hooks) == 1
    hook_cfg = hooks[0]
    assert "gitleaks_pre_commit.py" in hook_cfg["entry"]
    assert hook_cfg.get("stages") == ["pre-commit", "pre-push"]
