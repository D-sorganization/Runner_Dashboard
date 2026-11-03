from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from staff.worktree_git_lock_cleanup import clear_stale_git_locks, resolve_git_common_dir


def _git(args: list[str], *, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_worktree_with_submodule_git_dir(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "wt"
    root.mkdir()
    _git(["init", "-q", "-b", "main"], cwd=root)
    _git(["config", "user.email", "t@example.invalid"], cwd=root)
    _git(["config", "user.name", "Test"], cwd=root)
    (root / "README.md").write_text("x\n", encoding="utf-8")
    _git(["add", "README.md"], cwd=root)
    _git(["commit", "-qm", "init"], cwd=root)
    sub_git = root / ".git" / "modules" / "vendor" / "ud-tools"
    sub_git.mkdir(parents=True)
    stale_sub_lock = sub_git / "index.lock"
    stale_sub_lock.write_text("", encoding="utf-8")
    top_git = resolve_git_common_dir(root, subprocess.run)
    assert top_git is not None
    stale_top_lock = top_git / "config.lock"
    stale_top_lock.write_text("", encoding="utf-8")
    return root, stale_sub_lock, stale_top_lock


@pytest.mark.unit
def test_clear_stale_git_locks_removes_unlocked_repo_locks(tmp_path: Path) -> None:
    root, sub_lock, top_lock = _init_worktree_with_submodule_git_dir(tmp_path)

    result = clear_stale_git_locks(root, live_common_dirs=set())

    assert not sub_lock.exists()
    assert not top_lock.exists()
    assert str(sub_lock) in result.removed
    assert str(top_lock) in result.removed
    assert result.kept_live == ()
    assert result.errors == ()


@pytest.mark.unit
def test_clear_stale_git_locks_keeps_lock_when_live_git_uses_repo(tmp_path: Path) -> None:
    root, sub_lock, top_lock = _init_worktree_with_submodule_git_dir(tmp_path)
    sub_git = sub_lock.parent.resolve()

    result = clear_stale_git_locks(root, live_common_dirs={sub_git})

    assert sub_lock.exists()
    assert not top_lock.exists()
    assert str(sub_lock) in result.kept_live
    assert str(top_lock) in result.removed


@pytest.mark.unit
def test_bootstrap_preflight_clears_stale_lock_before_submodule_update(tmp_path: Path) -> None:
    from staff import runner_ops

    root, sub_lock, _top_lock = _init_worktree_with_submodule_git_dir(tmp_path)
    (root / ".gitmodules").write_text(
        '[submodule "vendor/ud-tools"]\n\tpath = vendor/ud-tools\n',
        encoding="utf-8",
    )
    (root / "scripts").mkdir()
    (root / "scripts" / "verify_merge_gates.py").write_text("# marker\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if (
            len(command) >= 5
            and command[0] == "git"
            and command[1] == "-C"
            and str(command[2]) == str(root)
            and command[3:5] == ["rev-parse", "--git-common-dir"]
        ):
            return subprocess.CompletedProcess(command, 0, stdout=".git\n")
        if command[:2] == ["git", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="git version 2.47.1\n")
        if command[:3] == ["git", "submodule", "status"]:
            return subprocess.CompletedProcess(command, 0, stdout="-abc123 vendor/ud-tools\n")
        if command[:2] == ["git", "worktree"]:
            return subprocess.CompletedProcess(command, 0, stdout=f"worktree {root}\n")
        if command[:3] == ["python3.11", "-c", runner_ops._PYTHON_VERSION_SNIPPET]:
            return subprocess.CompletedProcess(command, 0, stdout="3.11\n")
        if command[:3] == ["python3.11", "-m", "pytest"] and "--version" in command:
            return subprocess.CompletedProcess(command, 0, stdout="pytest 9.0.3\n")
        if "submodule" in command and "update" in command:
            return subprocess.CompletedProcess(command, 0)
        raise AssertionError(f"unexpected command: {command}")

    assert sub_lock.exists()
    result = runner_ops.run_worktree_bootstrap_preflight(
        root,
        run=fake_run,
        which=lambda name: "python3.11" if name == "python3.11" else None,
        live_git_common_dirs=set(),
    )

    assert result.ok is True
    assert not sub_lock.exists()
    assert any("submodule" in cmd and "update" in cmd for cmd in calls)
