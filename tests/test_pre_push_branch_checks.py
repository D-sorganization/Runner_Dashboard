from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import pre_push_branch_checks


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, text=True, capture_output=True).stdout.strip()


@pytest.mark.parametrize("changed", [False, True])
def test_merge_of_main_excludes_legacy_files_but_keeps_branch_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: bool
) -> None:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    (tmp_path / "base").write_text("base", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    git(tmp_path, "checkout", "-b", "feature")
    (tmp_path / "branch.md").write_text("bad format", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "branch")
    git(tmp_path, "checkout", "main")
    (tmp_path / "legacy.md").write_text("bad fragment", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "legacy")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(tmp_path, "checkout", "feature")
    git(tmp_path, "merge", "--no-edit", "main")
    if changed:
        (tmp_path / "legacy.md").write_text("branch modification", encoding="utf-8")
        git(tmp_path, "add", ".")
        git(tmp_path, "commit", "-m", "modify legacy")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PRE_COMMIT_TO_REF", raising=False)
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] == "git":
            return subprocess.run(command, **kwargs)
        calls.append(command)
        files = git(tmp_path, "diff", "--name-only", command[-3], command[-1]).splitlines()
        assert "branch.md" in files
        assert ("legacy.md" in files) == changed
        return subprocess.CompletedProcess(command, 1 if changed else 0)

    assert pre_push_branch_checks.main(run=run) == (1 if changed else 0)
    assert [call[2] for call in calls] == ["changes-fragment", "prettier"]


def test_missing_merge_base_fails_closed() -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "missing ref")

    assert pre_push_branch_checks.main(run=run) != 0


@pytest.mark.parametrize("failing_hook", ["changes-fragment", "prettier"])
def test_either_branch_hook_failure_blocks_push(failing_hook: str) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] == "git":
            return subprocess.CompletedProcess(command, 0, "base\n", "")
        return subprocess.CompletedProcess(command, int(command[2] == failing_hook))

    assert pre_push_branch_checks.main(run=run) != 0
