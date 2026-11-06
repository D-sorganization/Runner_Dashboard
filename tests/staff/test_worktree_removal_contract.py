"""Cleanup never treats unknown Git state or refused removal as disposable."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from staff import workspace
from staff import worktree_hygiene as worktree_hygiene_mod


@pytest.mark.parametrize("failure", ["status", "revision", "empty-count", "prefix-note"])
def test_uncertain_or_unpublished_git_state_is_preserved(monkeypatch, tmp_path, failure):
    def run(argv, **kwargs):
        if "status" in argv:
            output = "?? .staff-notes\n" if failure == "prefix-note" else ""
            return subprocess.CompletedProcess(argv, int(failure == "status"), output)
        if "symbolic-ref" in argv:
            return subprocess.CompletedProcess(argv, 1, "")
        return subprocess.CompletedProcess(argv, int(failure == "revision"), "" if failure == "empty-count" else "0")

    monkeypatch.setattr(workspace.subprocess, "run", run)
    assert workspace.worktree_has_unpushed_commits(tmp_path)


def test_git_refusal_preserves_worktree_and_metadata(monkeypatch, tmp_path):
    root = tmp_path / "runs"
    tree = root / "Tools-run-abc"
    tree.mkdir(parents=True)
    (tree / ".git").write_text("gitdir: synthetic-fixture\n")
    (tree / "sentinel").write_bytes(b"unpublished evidence")
    calls = []

    def refuse(argv, **kwargs):
        calls.append(argv)
        if "diff-files" in argv or "diff-index" in argv:
            # Uncertain/dirty tree: never eligible for force removal
            return subprocess.CompletedProcess(argv, 1, "", "")
        return subprocess.CompletedProcess(argv, 128, "", "locked or dirty")

    monkeypatch.setattr(workspace, "staff_worktrees_root", lambda: root)
    monkeypatch.setattr(worktree_hygiene_mod.subprocess, "run", refuse)
    monkeypatch.setattr(workspace, "prune_worktree_branch", lambda *args: pytest.fail("pruned after refusal"))
    workspace.remove_worktree(tree, checkout=tmp_path)
    assert (tree / "sentinel").read_bytes() == b"unpublished evidence"
    assert (tree / ".git").is_file()
    assert calls and all("--force" not in argv for argv in calls)


def test_submodule_debris_worktree_removed_with_safe_force(monkeypatch, tmp_path):
    """Normal remove fails on submodule/untracked paths; force succeeds when extras are submodule-only."""
    root = tmp_path / "runs"
    tree = root / "Tools-run-xyz"
    tree.mkdir(parents=True)
    (tree / ".git").write_text("gitdir: synthetic-fixture\n")
    (tree / ".gitmodules").write_text(
        '[submodule "child"]\n\tpath = vendor/child\n\turl = https://example.invalid/sub.git\n',
        encoding="utf-8",
    )
    calls = []

    def run_git(argv, **kwargs):
        calls.append(argv)
        cmd = " ".join(argv)
        if "worktree remove" in cmd and "--force" not in cmd:
            return subprocess.CompletedProcess(
                argv, 1, "", "fatal: cannot remove worktree: contains modified or untracked files"
            )
        if "worktree remove" in cmd and "--force" in cmd:
            shutil.rmtree(tree, ignore_errors=True)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "worktree prune" in cmd:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "diff-files" in cmd or "diff-index" in cmd:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:4] == ["git", "-C", str(tree), "ls-files"]:
            # Submodule checkout debris only (untracked under configured submodule path).
            return subprocess.CompletedProcess(argv, 0, "vendor/child/deep/file.bin\u0000", "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected argv")

    monkeypatch.setattr(workspace, "staff_worktrees_root", lambda: root)
    monkeypatch.setattr(worktree_hygiene_mod.subprocess, "run", run_git)
    monkeypatch.setattr(workspace, "prune_worktree_branch", lambda *args: None)
    monkeypatch.setattr(worktree_hygiene_mod.cli_projects, "remove_worktree_project", lambda *args: None)

    assert workspace.remove_worktree(tree, checkout=tmp_path) is True
    assert any("--force" in argv for argv in calls)


def test_untracked_user_evidence_blocks_force_even_when_tracked_clean(monkeypatch, tmp_path):
    """Review #4142: diff-files/diff-index clean is not enough — arbitrary untracked user files must survive."""
    root = tmp_path / "runs"
    tree = root / "Tools-run-evidence"
    tree.mkdir(parents=True)
    (tree / ".git").write_text("gitdir: synthetic-fixture\n")
    (tree / ".gitmodules").write_text(
        '[submodule "child"]\n\tpath = vendor/child\n\turl = https://example.invalid/sub.git\n',
        encoding="utf-8",
    )
    calls = []

    def run_git(argv, **kwargs):
        calls.append(argv)
        cmd = " ".join(argv)
        if "worktree remove" in cmd and "--force" not in cmd:
            return subprocess.CompletedProcess(
                argv, 1, "", "fatal: cannot remove worktree: contains modified or untracked files"
            )
        if "worktree remove" in cmd and "--force" in cmd:
            return subprocess.CompletedProcess(argv, 0, "", "")  # would wrongly delete evidence
        if "diff-files" in cmd or "diff-index" in cmd:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:4] == ["git", "-C", str(tree), "ls-files"]:
            # Untracked user note at repo root — not under submodule path and not durable transcript.
            return subprocess.CompletedProcess(argv, 0, "MY_UNPUBLISHED_NOTES.md\u0000vendor/child/x\u0000", "")
        return subprocess.CompletedProcess(argv, 1, "", f"unexpected argv: {cmd}")

    monkeypatch.setattr(workspace, "staff_worktrees_root", lambda: root)
    monkeypatch.setattr(worktree_hygiene_mod.subprocess, "run", run_git)
    monkeypatch.setattr(workspace, "prune_worktree_branch", lambda *args: pytest.fail("no prune when preserved"))
    workspace.remove_worktree(tree, checkout=tmp_path)
    assert tree.is_dir()
    assert all("--force" not in argv for argv in calls)


def test_untracked_ignored_user_evidence_blocks_force(monkeypatch, tmp_path):
    """Ignored paths are invisible to diff-index; still must not be wiped by forced worktree remove."""
    root = tmp_path / "runs"
    tree = root / "Tools-run-ignored"
    tree.mkdir(parents=True)
    (tree / ".git").write_text("gitdir: synthetic-fixture\n")
    calls = []

    def run_git(argv, **kwargs):
        calls.append(argv)
        cmd = " ".join(argv)
        if "worktree remove" in cmd and "--force" not in cmd:
            return subprocess.CompletedProcess(
                argv, 1, "", "fatal: cannot remove worktree: contains modified or untracked files"
            )
        if "worktree remove" in cmd and "--force" in cmd:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "diff-files" in cmd or "diff-index" in cmd:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:4] == ["git", "-C", str(tree), "ls-files"] and "--exclude-standard" in argv and "-i" in argv:
            return subprocess.CompletedProcess(argv, 0, "build/local-secret.env\u0000", "")
        if argv[:4] == ["git", "-C", str(tree), "ls-files"] and "--exclude-standard" in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", f"unexpected argv: {cmd}")

    monkeypatch.setattr(workspace, "staff_worktrees_root", lambda: root)
    monkeypatch.setattr(worktree_hygiene_mod.subprocess, "run", run_git)
    monkeypatch.setattr(workspace, "prune_worktree_branch", lambda *args: pytest.fail("no prune when preserved"))
    workspace.remove_worktree(tree, checkout=tmp_path)
    assert tree.is_dir()
    assert all("--force" not in argv for argv in calls)


def test_non_git_nonempty_directory_is_not_recursively_removed(tmp_path):
    tree = tmp_path / "unknown"
    tree.mkdir()
    (tree / "sentinel").write_text("keep")
    workspace.remove_worktree(tree)
    assert (tree / "sentinel").read_text() == "keep"


def test_linked_tree_is_preserved_before_removal(monkeypatch, tmp_path):
    root = tmp_path / "runs"
    tree = root / "Tools-run-linked"
    tree.mkdir(parents=True)
    (tree / ".git").write_text("gitdir: synthetic-fixture\n")
    sentinel = tree / "sentinel"
    sentinel.write_text("keep")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == tree or original(path))
    monkeypatch.setattr(workspace, "staff_worktrees_root", lambda: root)
    monkeypatch.setattr(workspace.subprocess, "run", lambda *args, **kwargs: pytest.fail("Git called on link"))
    workspace.remove_worktree(tree)
    assert sentinel.read_text() == "keep"
