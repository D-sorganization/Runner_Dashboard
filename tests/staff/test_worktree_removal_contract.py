"""Cleanup never treats unknown Git state or refused removal as disposable."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from staff import workspace


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
        return subprocess.CompletedProcess(argv, 128, "", "locked or dirty")

    monkeypatch.setattr(workspace, "staff_worktrees_root", lambda: root)
    monkeypatch.setattr(workspace.subprocess, "run", refuse)
    monkeypatch.setattr(workspace, "prune_worktree_branch", lambda *args: pytest.fail("pruned after refusal"))
    workspace.remove_worktree(tree, checkout=tmp_path)
    assert (tree / "sentinel").read_bytes() == b"unpublished evidence"
    assert (tree / ".git").is_file()
    assert calls and all("--force" not in argv for argv in calls)


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
