"""Direct tests for ``staff.rm_sync_quarantine`` on real local Git trees (#2976).

``test_staff_rm_sync`` covers plain untracked and unstaged edits through ``refresh``. These
pin the paths Git C-quotes in porcelain output (spaces, non-ASCII), staged changes and
renames: each must be saved aside and leave a clean tree, or rm_sync skips forever.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from staff.rm_sync import _git, _git_bytes
from staff.rm_sync_quarantine import quarantine_dirty_worktree

pytestmark = pytest.mark.unit


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "test.gitconfig"
    config.write_text("[user]\n name = Test\n email = test@example.invalid\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "rm"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    (root / "old.txt").write_text("old\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    return root


def _quarantine(root: Path, tmp_path: Path) -> dict | None:
    return quarantine_dirty_worktree(root, git_run=_git, git_bytes=_git_bytes, quarantine_parent=tmp_path / "q")


def _is_clean(root: Path) -> bool:
    return git(root, "status", "--porcelain", "--untracked-files=all") == ""


def test_clean_tree_is_left_alone(repo: Path, tmp_path: Path) -> None:
    assert _quarantine(repo, tmp_path) is None
    assert not (tmp_path / "q").exists()


@pytest.mark.parametrize(
    "name",
    [
        "notes draft.md",
        "café.txt",
        pytest.param('say "hi".txt', marks=pytest.mark.requires_posix),
    ],
)
def test_untracked_paths_git_quotes_are_saved_and_cleaned(repo: Path, tmp_path: Path, name: str) -> None:
    (repo / name).write_text("owner note\n", encoding="utf-8")
    summary = _quarantine(repo, tmp_path)
    assert summary is not None and summary["paths"] == [name]
    assert (Path(summary["dir"]) / name).read_text(encoding="utf-8") == "owner note\n"
    assert _is_clean(repo)


def test_unstaged_edit_and_deletion_are_saved_and_restored(repo: Path, tmp_path: Path) -> None:
    (repo / "tracked.txt").write_text("edited\n", encoding="utf-8")
    (repo / "old.txt").unlink()
    summary = _quarantine(repo, tmp_path)
    assert summary is not None
    saved = Path(summary["dir"])
    assert (saved / "tracked.txt").read_text(encoding="utf-8") == "edited\n"
    manifest = json.loads((saved / "manifest.json").read_text(encoding="utf-8"))
    assert {"status": " D", "path": "old.txt"} in manifest["entries"]
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "base\n"
    assert (repo / "old.txt").exists()
    assert _is_clean(repo)


def test_staged_and_unstaged_edits_keep_both_versions(repo: Path, tmp_path: Path) -> None:
    (repo / "tracked.txt").write_text("base\nstaged\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_text("base\nstaged\nworktree\n", encoding="utf-8")
    summary = _quarantine(repo, tmp_path)
    assert summary is not None
    saved = Path(summary["dir"])
    assert (saved / "tracked.txt").read_text(encoding="utf-8") == "base\nstaged\nworktree\n"
    assert "+staged" in (saved / "staged.patch").read_text(encoding="utf-8")
    assert _is_clean(repo)


@pytest.mark.parametrize(
    "before,after",
    [
        (b"old\n\n\n\n", b"new\n\n\n\n"),
        (b"old\r\nline\r\n", b"new\r\nline\r\n"),
        (bytes(range(256)), bytes(reversed(range(256)))),
    ],
)
def test_staged_patch_restores_exact_bytes(repo: Path, tmp_path: Path, before: bytes, after: bytes) -> None:
    path = repo / "tracked.txt"
    path.write_bytes(before)
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-qm", "fixture")
    path.write_bytes(after)
    git(repo, "add", "tracked.txt")
    summary = _quarantine(repo, tmp_path)
    assert summary is not None
    patch = str(Path(summary["dir"]) / "staged.patch")
    git(repo, "apply", "--check", "--index", patch)
    git(repo, "apply", "--index", patch)
    assert path.read_bytes() == after
    assert (
        subprocess.run(["git", "-C", str(repo), "show", ":tracked.txt"], check=True, capture_output=True).stdout
        == after
    )


def test_staged_new_file_is_saved_and_removed(repo: Path, tmp_path: Path) -> None:
    (repo / "added.txt").write_text("new\n", encoding="utf-8")
    git(repo, "add", "added.txt")
    summary = _quarantine(repo, tmp_path)
    assert summary is not None
    assert (Path(summary["dir"]) / "added.txt").read_text(encoding="utf-8") == "new\n"
    assert not (repo / "added.txt").exists()
    assert _is_clean(repo)


def test_staged_rename_to_a_quoted_name_restores_the_original(repo: Path, tmp_path: Path) -> None:
    git(repo, "mv", "old.txt", "new name.txt")
    summary = _quarantine(repo, tmp_path)
    assert summary is not None
    saved = Path(summary["dir"])
    assert (saved / "new name.txt").read_text(encoding="utf-8") == "old\n"
    manifest = json.loads((saved / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["entries"] == [{"status": "R ", "path": "new name.txt", "orig_path": "old.txt"}]
    assert (repo / "old.txt").read_text(encoding="utf-8") == "old\n"
    assert not (repo / "new name.txt").exists()
    assert _is_clean(repo)


def test_no_staged_patch_without_index_changes(repo: Path, tmp_path: Path) -> None:
    (repo / "tracked.txt").write_text("edited\n", encoding="utf-8")
    summary = _quarantine(repo, tmp_path)
    assert summary is not None
    assert not (Path(summary["dir"]) / "staged.patch").exists()
