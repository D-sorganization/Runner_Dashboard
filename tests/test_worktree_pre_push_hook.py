from __future__ import annotations

import inspect
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.staff import git_hooks
from backend.staff.workspace import add_worktree


def _git(cwd: Path, *args: str, input: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=input,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    checkout = tmp_path / "checkout"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(tmp_path, "init", "-b", "main", str(checkout))
    _git(checkout, "config", "user.name", "Test Agent")
    _git(checkout, "config", "user.email", "agent@example.invalid")
    hook_source = Path(inspect.getsourcefile(git_hooks) or "")
    hook_target = checkout / "backend/staff/git_hooks.py"
    hook_target.parent.mkdir(parents=True)
    shutil.copyfile(hook_source, hook_target)
    (checkout / "README.md").write_text("base\n", encoding="utf-8")
    _git(checkout, "add", "backend/staff/git_hooks.py")
    _git(checkout, "add", "README.md")
    _git(checkout, "commit", "-m", "initial")
    _git(checkout, "remote", "add", "origin", str(origin))
    _git(checkout, "push", "-u", "origin", "main")
    return origin, checkout


def _commit(worktree: Path) -> None:
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "change")


def _push(worktree: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "push", "origin", "HEAD"],
        cwd=worktree,
        text=True,
        capture_output=True,
        input="",
    )


@pytest.mark.parametrize(
    ("branch", "expected"),
    [
        ("codex/2279-hook", "2279"),
        ("fix/issue-42-checks", "42"),
        ("chore/release-2026-10", None),
    ],
)
def test_issue_number_comes_from_branch_ticket_prefix(branch: str, expected: str | None) -> None:
    assert git_hooks._issue_number(branch) == expected


def test_add_worktree_installs_private_pre_push_hook(tmp_path: Path) -> None:
    _, checkout = _repository(tmp_path)
    worktree = tmp_path / "worktrees" / "codex-2279-hook"
    add_worktree(checkout, worktree, "codex/2279-hook")

    hooks_path = Path(_git(worktree, "config", "--worktree", "--get", "core.hooksPath"))
    git_dir = Path(_git(worktree, "rev-parse", "--git-dir")).resolve()
    hook = hooks_path / "pre-push"
    assert hooks_path == git_dir / "hooks"
    assert hook.is_file()
    assert os.access(hook, os.X_OK)
    assert _git(worktree, "config", "--get", "extensions.worktreeConfig") == "true"


def test_issue_branch_requires_change_fragment(tmp_path: Path) -> None:
    _, checkout = _repository(tmp_path)
    worktree = tmp_path / "worktrees" / "codex-2279-hook"
    add_worktree(checkout, worktree, "codex/2279-hook")
    (worktree / "change.py").write_text("value = 1\n", encoding="utf-8")
    _commit(worktree)

    result = _push(worktree)
    assert result.returncode != 0
    assert "changes/2279-" in result.stdout + result.stderr


def test_pre_push_lints_changed_python_and_allows_valid_fragment(tmp_path: Path) -> None:
    _, checkout = _repository(tmp_path)
    worktree = tmp_path / "worktrees" / "codex-2279-hook"
    add_worktree(checkout, worktree, "codex/2279-hook")
    (worktree / "change.py").write_text("value = 1\n", encoding="utf-8")
    (worktree / "changes").mkdir()
    (worktree / "changes/2279-hook.md").write_text("Hook checks.\n", encoding="utf-8")
    _commit(worktree)

    result = _push(worktree)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("source", "expected"),
    [("import os\n", "F401"), ("value=1\n", "Would reformat")],
)
def test_pre_push_rejects_ruff_failures(tmp_path: Path, source: str, expected: str) -> None:
    _, checkout = _repository(tmp_path)
    worktree = tmp_path / "worktrees" / "codex-2279-hook"
    add_worktree(checkout, worktree, "codex/2279-hook")
    (worktree / "bad.py").write_text(source, encoding="utf-8")
    (worktree / "changes").mkdir()
    (worktree / "changes/2279-hook.md").write_text("Hook checks.\n", encoding="utf-8")
    _commit(worktree)

    result = _push(worktree)
    assert result.returncode != 0
    assert expected in result.stdout + result.stderr


def test_pre_push_rejects_diff_at_500_lines(tmp_path: Path) -> None:
    _, checkout = _repository(tmp_path)
    worktree = tmp_path / "worktrees" / "codex-2279-hook"
    add_worktree(checkout, worktree, "codex/2279-hook")
    (worktree / "changes").mkdir()
    (worktree / "changes/2279-hook.md").write_text("Hook checks.\n", encoding="utf-8")
    (worktree / "large.txt").write_text("line\n" * 499, encoding="utf-8")
    _commit(worktree)

    result = _push(worktree)
    assert result.returncode != 0
    assert "500" in result.stdout + result.stderr
