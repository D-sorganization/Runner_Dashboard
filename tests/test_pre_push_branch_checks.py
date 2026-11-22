from __future__ import annotations

import shutil
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


# --- Integration: real pre-commit hooks, real git history (Runner_Dashboard#4609) ---

REPO_ROOT = Path(__file__).resolve().parents[1]
BAD_FRAGMENT = "no front matter here\n"
GOOD_FRAGMENT = '---\nissue: 1193\nsummary: "Valid fragment for the integration test."\n---\n'
UNFORMATTED_MD = "# Title\n\n*   item\n"


def _commit_all(root: Path, message: str) -> None:
    git(root, "add", ".")
    git(root, "commit", "-m", message)


def _build_merged_repo(root: Path, *, branch_files: dict[str, str]) -> None:
    """main gains invalid legacy files after the branch point; the branch is then merged with main."""
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "core.hooksPath", str(root.parent / "no-hooks"))  # fixture commits must not run hooks
    shutil.copytree(REPO_ROOT / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(REPO_ROOT / ".pre-commit-config.yaml", root / ".pre-commit-config.yaml")
    (root / "changes").mkdir()
    (root / "changes" / "README.md").write_text("# Changes\n", encoding="utf-8")
    _commit_all(root, "base")
    git(root, "checkout", "-b", "feature")
    for name, content in branch_files.items():
        (root / name).write_text(content, encoding="utf-8", newline="\n")
    _commit_all(root, "branch work")
    git(root, "checkout", "main")
    (root / "changes" / "9-legacy.md").write_text(BAD_FRAGMENT, encoding="utf-8", newline="\n")
    (root / "legacy.md").write_text(UNFORMATTED_MD, encoding="utf-8", newline="\n")
    _commit_all(root, "legacy debt already on main")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(root, "checkout", "feature")
    git(root, "merge", "--no-edit", "main")


@pytest.fixture
def real_hooks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    if shutil.which("pre-commit") is None:
        pytest.skip("pre-commit is not installed")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PRE_COMMIT_TO_REF", raising=False)
    return tmp_path


def test_real_hooks_block_broken_branch_fragment_and_ignore_legacy_files(
    real_hooks: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    _build_merged_repo(real_hooks, branch_files={"changes/1-branch.md": BAD_FRAGMENT})

    assert pre_push_branch_checks.main() != 0

    output = capfd.readouterr().out.replace("\\", "/")
    assert "changes/1-branch.md" in output
    assert "9-legacy.md" not in output


def test_real_hooks_block_unformatted_branch_file_and_leave_legacy_untouched(
    real_hooks: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    _build_merged_repo(real_hooks, branch_files={"branch.md": UNFORMATTED_MD})

    assert pre_push_branch_checks.main() != 0

    assert (real_hooks / "branch.md").read_text(encoding="utf-8") != UNFORMATTED_MD  # prettier rewrote it
    assert (real_hooks / "legacy.md").read_text(encoding="utf-8") == UNFORMATTED_MD  # untouched main file
    assert (real_hooks / "changes" / "9-legacy.md").read_text(encoding="utf-8") == BAD_FRAGMENT


def test_real_hooks_pass_when_only_main_files_are_invalid(real_hooks: Path) -> None:
    _build_merged_repo(real_hooks, branch_files={"changes/1193-ok.md": GOOD_FRAGMENT})

    assert pre_push_branch_checks.main() == 0
