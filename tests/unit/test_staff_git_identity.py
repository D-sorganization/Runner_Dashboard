"""Staff lane git author/committer identity."""

from __future__ import annotations

import inspect
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.staff.git_identity import staff_git_identity_env, staff_git_user_email, staff_git_user_name
from backend.staff.workspace import add_worktree

pytestmark = pytest.mark.unit


def test_staff_git_identity_defaults_to_fleet_app_bot() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("STAFF_GIT_USER_NAME", None)
        os.environ.pop("STAFF_GIT_USER_EMAIL", None)
        assert staff_git_user_name() == "d-sorggeneralagent[bot]"
        assert staff_git_user_email().endswith("@users.noreply.github.com")
        assert "d-sorggeneralagent" in staff_git_user_email()


def test_staff_git_identity_env_exports_author_and_committer() -> None:
    with patch.dict(
        os.environ,
        {"STAFF_GIT_USER_NAME": "Fleet Bot", "STAFF_GIT_USER_EMAIL": "bot@users.noreply.github.com"},
        clear=False,
    ):
        env = staff_git_identity_env()
    assert env == {
        "GIT_AUTHOR_NAME": "Fleet Bot",
        "GIT_AUTHOR_EMAIL": "bot@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "Fleet Bot",
        "GIT_COMMITTER_EMAIL": "bot@users.noreply.github.com",
    }


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    checkout = tmp_path / "checkout"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(tmp_path, "init", "-b", "main", str(checkout))
    _git(checkout, "config", "user.name", "Owner Human")
    _git(checkout, "config", "user.email", "owner@users.noreply.github.com")
    from backend.staff import git_hooks

    hook_source = Path(inspect.getsourcefile(git_hooks) or "")
    hook_target = checkout / "backend/staff/git_hooks.py"
    hook_target.parent.mkdir(parents=True)
    shutil.copyfile(hook_source, hook_target)
    (checkout / "README.md").write_text("base\n", encoding="utf-8")
    _git(checkout, "add", "backend/staff/git_hooks.py", "README.md")
    _git(checkout, "commit", "-m", "initial")
    _git(checkout, "remote", "add", "origin", str(origin))
    _git(checkout, "push", "-u", "origin", "main")
    return checkout


def test_add_worktree_sets_bot_git_identity_over_owner_checkout_config(tmp_path: Path) -> None:
    checkout = _repository(tmp_path)
    worktree = tmp_path / "worktrees" / "staff-lane"
    add_worktree(checkout, worktree, "staff/git-identity-test")

    assert _git(worktree, "config", "--worktree", "--get", "user.name") == staff_git_user_name()
    assert _git(worktree, "config", "--worktree", "--get", "user.email") == staff_git_user_email()

    (worktree / "lane.txt").write_text("lane work\n", encoding="utf-8")
    _git(worktree, "add", "lane.txt")
    _git(worktree, "commit", "-m", "lane commit")
    assert staff_git_user_email() in _git(worktree, "log", "-1", "--format=%ae")
