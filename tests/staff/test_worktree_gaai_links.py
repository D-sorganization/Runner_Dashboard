"""Tracked GAAI links are disposable; unsafe links retain evidence (#4224)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from staff import cli_projects, workspace
from staff import worktree_hygiene as hygiene
from staff.store import RunRecord, RunStore


@pytest.fixture
def linked_tree(tmp_path, monkeypatch):
    repo, root = tmp_path / "repo", tmp_path / "runs"
    repo.mkdir()
    root.mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, timeout=20
        ).stdout

    git("init", "-b", "main")
    (repo / ".gitignore").write_text(".staff/\n")
    outside = tmp_path / "external"
    outside.mkdir()
    (outside / "sentinel").write_text("keep external evidence")
    (repo / ".gaai").mkdir()
    for name in ("agents", "workflows", "contexts"):
        (repo / ".gaai" / name).symlink_to(outside, target_is_directory=True)
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@users.noreply.github.com", "commit", "-m", "fixture")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    tree = root / "Tools-run-finished"
    git("worktree", "add", "-b", "staff/finished", str(tree), "HEAD")
    (tree / ".staff").mkdir()
    (tree / ".staff" / "transcript.log").write_text("durable transcript")
    store = RunStore(tmp_path / "runs.sqlite3")
    store.create_run(
        RunRecord(
            "run-finished",
            "test",
            "codex",
            None,
            "fixture",
            "Tools",
            "issue",
            "#4224",
            "task",
            status="succeeded",
            workdir=str(tree),
        )
    )
    monkeypatch.setattr(workspace, "staff_worktrees_root", lambda: root)
    monkeypatch.setattr(workspace, "find_repo_checkout", lambda name: repo)
    monkeypatch.setattr(cli_projects, "remove_worktree_project", lambda *args: None)
    yield SimpleNamespace(repo=repo, root=root, tree=tree, store=store, outside=outside, git=git)
    store.close()


@pytest.mark.parametrize("entrypoint", ["remove", "finalize", "sweep", "cordon"])
def test_finished_tracked_gaai_links_are_unlinked_without_following(linked_tree, monkeypatch, entrypoint):
    c = linked_tree
    if entrypoint == "remove":
        saved = hygiene.persist_run_transcript(c.store, "run-finished", c.tree)
        assert hygiene.remove_worktree_checked(c.tree, c.repo, "staff/finished", saved)
    elif entrypoint == "finalize":
        hygiene.finalize_run_worktree(c.store, "run-finished", c.tree, "Tools")
    elif entrypoint == "sweep":
        assert hygiene.sweep_stale_worktrees(c.store)["removed"] == 1
    else:
        from disk_full_cordon_runtime import _run_cleanup_commands
        from staff import store as staff_store

        c.store.create_run(
            RunRecord(
                "run-active",
                "test",
                "codex",
                None,
                "fixture",
                "Tools",
                "issue",
                "#4224",
                "task",
                status="running",
                workdir=str(c.root / "Tools-run-active"),
            )
        )
        c.git("worktree", "add", "-b", "staff/active", str(c.root / "Tools-run-active"), "HEAD")
        monkeypatch.setattr(staff_store, "get_run_store", lambda: c.store)
        _run_cleanup_commands(SimpleNamespace(cleanup=()), dry_run=False)
        assert (c.root / "Tools-run-active").is_dir()
    assert not c.tree.exists()
    assert (c.outside / "sentinel").read_text() == "keep external evidence"
    assert Path(c.store.get_run("run-finished").transcript_path).read_text() == "durable transcript"


@pytest.mark.parametrize("entrypoint", ["remove", "finalize", "sweep"])
@pytest.mark.parametrize("hazard", ["untracked", "elsewhere", "ancestor", "reparse", "index-only", "gaai-parent"])
def test_unsafe_links_are_visible_and_preserved(linked_tree, monkeypatch, caplog, hazard, entrypoint):
    c = linked_tree
    if hazard in {"untracked", "index-only"}:
        c.git("rm", ".gaai/agents")
        c.git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@users.noreply.github.com",
            "commit",
            "-m",
            "remove link",
        )
        (c.tree / ".gaai" / "agents").unlink()
        subprocess.run(["git", "-C", str(c.tree), "reset", "--hard", "main"], check=True, capture_output=True)
        (c.tree / ".gaai" / "agents").symlink_to(c.outside, target_is_directory=True)
        if hazard == "index-only":
            subprocess.run(["git", "-C", str(c.tree), "add", ".gaai/agents"], check=True, capture_output=True)
    elif hazard == "elsewhere":
        (c.tree / "unexpected").symlink_to(c.outside, target_is_directory=True)
    elif hazard == "ancestor":
        original = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda p: p == c.root or original(p))
    elif hazard == "gaai-parent":
        import shutil

        shutil.rmtree(c.tree / ".gaai")
        (c.tree / ".gaai").symlink_to(c.outside, target_is_directory=True)
    else:
        original = Path.lstat

        def reparse(p, *args, **kwargs):
            if p == c.tree / ".gaai" / "agents":
                return SimpleNamespace(st_file_attributes=0x400)
            return original(p, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", reparse)
    if entrypoint == "remove":
        assert not hygiene.remove_worktree_checked(c.tree, c.repo, "staff/finished")
    elif entrypoint == "finalize":
        hygiene.finalize_run_worktree(c.store, "run-finished", c.tree, "Tools")
    else:
        stats = hygiene.sweep_stale_worktrees(c.store)
        assert stats["preserved_links"] == 1
    assert c.tree.exists()
    assert str(c.tree) in caplog.text
    assert "link" in caplog.text.lower() or "reparse" in caplog.text.lower()
    if entrypoint != "remove":
        assert any("preserved" in e["text"] for e in c.store.events_after("run-finished"))
    assert (c.outside / "sentinel").read_text() == "keep external evidence"


@pytest.mark.parametrize("guard", ["active", "orphaned", "unpushed", "dirty", "transcript", "nested"])
def test_tracked_gaai_exception_keeps_existing_guards(linked_tree, guard):
    c = linked_tree
    if guard == "active":
        c.store.update_run("run-finished", status="running")
    elif guard == "orphaned":
        c.store.update_run("run-finished", status="failed", failure_class="orphaned")
    elif guard == "unpushed":
        subprocess.run(
            [
                "git",
                "-C",
                str(c.tree),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@users.noreply.github.com",
                "commit",
                "--allow-empty",
                "-m",
                "unpushed",
            ],
            check=True,
            capture_output=True,
        )
    elif guard == "dirty":
        (c.tree / ".gitignore").write_text("changed")
    elif guard == "transcript":
        (c.tree / ".staff" / "transcript.log").unlink()
    else:
        nested = c.root / "nested" / c.tree.name
        nested.parent.mkdir()
        c.git("worktree", "move", str(c.tree), str(nested))
        c.tree = nested
        c.store.update_run("run-finished", workdir=str(nested))
    hygiene.finalize_run_worktree(c.store, "run-finished", c.tree, "Tools")
    assert hygiene.sweep_stale_worktrees(c.store)["removed"] == 0
    assert c.tree.is_dir()
    assert (c.outside / "sentinel").read_text() == "keep external evidence"
