"""Preservation contracts use real run records and fixture-only deletion."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from staff import cli_projects, workspace
from staff import worktree_hygiene as hygiene
from staff.store import RunRecord, RunStore


def _record(run_id: str, tree: Path) -> RunRecord:
    return RunRecord(
        run_id,
        "test",
        "codex",
        None,
        "fixture",
        "Tools",
        "issue",
        "#2646",
        "task",
        status="succeeded",
        workdir=str(tree),
    )


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    tree = root / "Tools-run-finished"
    (tree / ".staff").mkdir(parents=True)
    (tree / ".git").write_text("gitdir: synthetic-fixture\n")
    (tree / ".staff" / "transcript.log").write_bytes(b"original transcript\n")
    store = RunStore(tmp_path / "runs.sqlite3")
    store.create_run(_record("run-finished", tree))
    calls = []
    real_remove, real_git_check = workspace.remove_worktree, workspace.worktree_has_unpushed_commits
    monkeypatch.setattr(workspace, "staff_worktrees_root", lambda: root)
    monkeypatch.setattr(workspace, "find_repo_checkout", lambda repo: None)
    monkeypatch.setattr(workspace, "worktree_has_unpushed_commits", lambda path: False)
    monkeypatch.setattr(workspace, "remove_worktree", lambda path, **kwargs: calls.append(path))
    yield SimpleNamespace(
        root=root, tree=tree, store=store, calls=calls, real_remove=real_remove, real_git_check=real_git_check
    )
    store.close()


@pytest.mark.parametrize("authority", [None, object(), "query-failure"])
def test_sweep_requires_authoritative_active_runs(candidate, monkeypatch, authority):
    def unavailable():
        raise RuntimeError("active store unavailable")

    if authority == "query-failure":
        monkeypatch.setattr(candidate.store, "active_runs", unavailable)
        authority = candidate.store
    hygiene.sweep_stale_worktrees(authority)
    assert candidate.calls == []
    assert (candidate.tree / ".staff" / "transcript.log").read_bytes() == b"original transcript\n"


@pytest.mark.parametrize("entrypoint", ["sweep", "finalize"])
@pytest.mark.parametrize(
    "hazard",
    (
        "dirty missing-record mismatched-workdir active live-pid orphaned linked "
        "persist-none store-write store-readback copy-corruption"
    ).split(),
)
def test_uncertain_finished_tree_keeps_original(candidate, monkeypatch, entrypoint, hazard):
    store, tree = candidate.store, candidate.tree
    if hazard == "dirty":
        monkeypatch.setattr(workspace, "worktree_has_unpushed_commits", lambda path: True)
    elif hazard == "missing-record":
        monkeypatch.setattr(store, "get_run", lambda run_id: None)
    elif hazard == "mismatched-workdir":
        store.update_run("run-finished", workdir=str(tree.parent / "another"))
    elif hazard == "active":
        store.update_run("run-finished", status="running")
    elif hazard == "live-pid":
        store.update_run("run-finished", pid=12345)
        monkeypatch.setattr(hygiene.psutil, "pid_exists", lambda pid: True)
    elif hazard == "orphaned":
        store.update_run("run-finished", status="failed", failure_class="orphaned")
    elif hazard == "linked":
        original = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda path: path == tree or original(path))
        monkeypatch.setattr(
            workspace, "worktree_has_unpushed_commits", lambda path: pytest.fail("Git inspected a link")
        )
    elif hazard == "persist-none":
        monkeypatch.setattr(hygiene, "persist_run_transcript", lambda *args: None)
    elif hazard == "store-write":

        def unavailable(*args, **kwargs):
            raise RuntimeError("metadata unavailable")

        monkeypatch.setattr(store, "update_run", unavailable)
    elif hazard == "store-readback":
        monkeypatch.setattr(store, "update_run", lambda *args, **kwargs: None)
    else:
        monkeypatch.setattr(hygiene.shutil, "copyfileobj", lambda source, output: output.write(b"corrupt"))
    if entrypoint == "sweep":
        hygiene.sweep_stale_worktrees(store)
    else:
        hygiene.finalize_run_worktree(store, "run-finished", tree, "Tools")
    assert candidate.calls == []
    assert (tree / ".staff" / "transcript.log").read_bytes() == b"original transcript\n"


def test_removal_refusal_is_not_counted_and_keeps_original(candidate):
    stats = hygiene.sweep_stale_worktrees(candidate.store)
    assert candidate.calls == [candidate.tree]
    assert stats["removed"] == 0
    assert (candidate.tree / ".staff" / "transcript.log").read_bytes() == b"original transcript\n"


def test_missing_transcript_keeps_other_evidence(candidate):
    (candidate.tree / ".staff" / "transcript.log").unlink()
    (candidate.tree / ".staff" / "result.json").write_text("evidence")
    hygiene.finalize_run_worktree(candidate.store, "run-finished", candidate.tree, "Tools")
    assert candidate.calls == []
    assert (candidate.tree / ".staff" / "result.json").read_text() == "evidence"


def test_persisted_transcript_uses_the_actual_store_directory(candidate):
    saved = hygiene.persist_run_transcript(candidate.store, "run-finished", candidate.tree)
    assert saved.parent == candidate.store.path.parent / "transcripts"
    assert saved.read_bytes() == b"original transcript\n"


def test_failed_replace_and_temporary_cleanup_preserve_finalization(candidate, monkeypatch):
    destination = candidate.store.path.parent / "transcripts"
    monkeypatch.setattr(candidate.store, "db_path", candidate.store.path, raising=False)
    replace, unlink = os.replace, Path.unlink

    def refuse_replace(source, target):
        if Path(target).parent == destination:
            raise PermissionError("replace unavailable")
        return replace(source, target)

    def refuse_unlink(path, *args, **kwargs):
        if path.parent == destination:
            raise PermissionError("temporary file unavailable")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "replace", refuse_replace)
    monkeypatch.setattr(Path, "unlink", refuse_unlink)
    assert hygiene.persist_run_transcript(candidate.store, "run-finished", candidate.tree) is None
    assert (candidate.tree / ".staff" / "transcript.log").read_bytes() == b"original transcript\n"


@pytest.mark.parametrize("kind", ["scratch", "cloud", "outside"])
def test_owned_non_git_finalization_still_persists_transcript(candidate, kind):
    target = {"scratch": candidate.tree, "cloud": candidate.tree / "cloud", "outside": candidate.root.parent / "owned"}[
        kind
    ]
    (target / ".staff").mkdir(parents=True, exist_ok=True)
    if kind == "scratch":
        (target / ".git").unlink()
    (target / ".staff" / "transcript.log").write_bytes(b"owned non-Git transcript\n")
    candidate.store.update_run("run-finished", workdir=str(target))
    hygiene.finalize_run_worktree(candidate.store, "run-finished", target, "Tools")
    saved = Path(candidate.store.get_run("run-finished").transcript_path)
    assert saved.read_bytes() == b"owned non-Git transcript\n"
    assert target.is_dir() and candidate.calls == []


@pytest.mark.parametrize("locked", [False, True])
def test_registered_git_worktree_respects_lock_and_preserves_transcript(candidate, monkeypatch, tmp_path, locked):
    repo, tree = tmp_path / "repository", candidate.root / "Tools-run-real"
    repo.mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=20)

    git("init", "-b", "main")
    (repo / ".gitignore").write_text(".staff/\n")
    git("add", ".gitignore")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("worktree", "add", "-b", "staff/finished", str(tree), "HEAD")
    if locked:
        git("worktree", "lock", str(tree))
    (tree / ".staff").mkdir()
    (tree / ".staff" / "transcript.log").write_bytes(b"durable fixture transcript\n")
    candidate.store.create_run(_record("run-real", tree))
    monkeypatch.setattr(workspace, "remove_worktree", candidate.real_remove)
    monkeypatch.setattr(workspace, "worktree_has_unpushed_commits", candidate.real_git_check)
    monkeypatch.setattr(workspace, "find_repo_checkout", lambda name: repo)
    monkeypatch.setattr(cli_projects, "remove_worktree_project", lambda *args: None)
    hygiene.finalize_run_worktree(candidate.store, "run-real", tree, "Tools")
    assert tree.exists() is locked
    if locked:
        assert (tree / ".staff" / "transcript.log").read_bytes() == b"durable fixture transcript\n"
    saved = Path(candidate.store.get_run("run-real").transcript_path)
    assert saved.read_bytes() == b"durable fixture transcript\n"
