"""In-tree links are disposable; linked roots and uncertain state retain evidence."""

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
@pytest.mark.parametrize("hazard", ["ancestor", "reparse", "inspection"])
def test_unsafe_links_are_visible_and_preserved(linked_tree, monkeypatch, caplog, hazard, entrypoint):
    c = linked_tree
    if hazard == "ancestor":
        original = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda p: p == c.root or original(p))
    else:
        original = Path.lstat

        def inspect(p, *args, **kwargs):
            if p == c.root:
                if hazard == "inspection":
                    raise PermissionError("inspection denied")
                return SimpleNamespace(st_file_attributes=0x400)
            return original(p, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", inspect)
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


@pytest.mark.parametrize(
    "guard", ["active", "blocked", "needs_input", "orphaned", "unpushed", "dirty", "transcript", "nested"]
)
def test_tracked_gaai_exception_keeps_existing_guards(linked_tree, guard):
    c = linked_tree
    if guard == "active":
        c.store.update_run("run-finished", status="running")
    elif guard in {"blocked", "needs_input"}:
        c.store.update_run("run-finished", status=guard)
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


@pytest.mark.parametrize(
    "relative,target",
    [
        ("outside-link", "external"),
        (".venv/lib64", "lib"),
        (".gaai/scripts", "external"),
        (".gaai/skills", "external"),
        ("dangling", "missing"),
    ],
)
def test_sweep_unlinks_in_tree_links_and_records_targets(linked_tree, relative, target, caplog):
    c = linked_tree
    link = c.tree / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    if target == "lib":
        (link.parent / "lib").mkdir()
        (link.parent / "lib" / "module.py").write_text("# fixture")
    destination = str(c.outside) if target == "external" else target
    link.symlink_to(destination, target_is_directory=True)
    # Ignored environment links must be disposable without changing tracked state.
    with (c.repo / ".git" / "info" / "exclude").open("a") as exclude:
        exclude.write(f"\n/{relative.split('/')[0]}/\n" if "/" in relative else f"\n/{relative}\n")
    with caplog.at_level("INFO"):
        assert hygiene.sweep_stale_worktrees(c.store)["removed"] == 1
    assert not c.tree.exists()
    assert (c.outside / "sentinel").read_text() == "keep external evidence"
    assert f"{relative} -> {destination} (target untouched)" in caplog.text


def test_locked_linked_worktree_keeps_link_entries(linked_tree):
    c = linked_tree
    c.git("worktree", "lock", str(c.tree))
    assert hygiene.sweep_stale_worktrees(c.store)["removed"] == 0
    assert (c.tree / ".gaai" / "agents").is_symlink()


def test_explicitly_held_workdir_keeps_link_entries(linked_tree):
    c = linked_tree
    stats = hygiene.sweep_stale_worktrees(c.store, active_workdirs={str(c.tree)})
    assert stats["skipped_active"] == 1
    assert (c.tree / ".gaai" / "agents").is_symlink()


@pytest.mark.parametrize("ancestor", [False, True])
def test_real_root_or_ancestor_link_is_refused(linked_tree, ancestor):
    from staff.worktree_links import refuse_worktree_links

    c = linked_tree
    alias = c.root.parent / "alias"
    alias.symlink_to(c.root if ancestor else c.tree, target_is_directory=True)
    assert refuse_worktree_links(alias / c.tree.name if ancestor else alias)
    assert c.tree.is_dir()
    assert (c.outside / "sentinel").is_file()


def test_readlink_failure_leaves_all_entries_intact(linked_tree, monkeypatch):
    c = linked_tree
    monkeypatch.setattr(os, "readlink", lambda path: (_ for _ in ()).throw(PermissionError("denied")))
    assert hygiene.sweep_stale_worktrees(c.store)["preserved_links"] == 1
    assert (c.tree / ".gaai" / "agents").is_symlink()


def test_in_tree_windows_reparse_entry_is_removed_without_descent(linked_tree, monkeypatch, caplog):
    from staff import worktree_links

    c = linked_tree
    junction = c.tree / "junction"
    junction.symlink_to(c.outside, target_is_directory=True)
    original_lstat, original_rmdir = Path.lstat, Path.rmdir

    def metadata(path, *args, **kwargs):
        if path == junction:
            return SimpleNamespace(st_file_attributes=0x410)
        return original_lstat(path, *args, **kwargs)

    removed = []

    def remove_entry(path, *args, **kwargs):
        if path == junction:
            removed.append(path)
            return path.unlink()  # emulate Windows rmdir(junction) on POSIX
        return original_rmdir(path, *args, **kwargs)

    def scan(path):
        assert Path(path) != junction, "must not descend into a reparse point"
        return os.scandir(path)

    monkeypatch.setattr(Path, "lstat", metadata)
    monkeypatch.setattr(Path, "rmdir", remove_entry)
    monkeypatch.setattr(worktree_links, "os", SimpleNamespace(name="nt", scandir=scan, readlink=os.readlink))
    with caplog.at_level("INFO"):
        worktree_links.unlink_worktree_links(c.tree)
    assert removed == [junction]
    assert not junction.exists()
    assert (c.outside / "sentinel").read_text() == "keep external evidence"
    assert "Windows reparse point junction" in caplog.text


def test_transient_force_remove_restores_tracked_gaai_symlinks(linked_tree, monkeypatch):
    c = linked_tree
    agents = c.tree / ".gaai" / "agents"
    assert agents.is_symlink()

    original_out = hygiene._git_out

    def fake_git_out(cwd, *args, **kwargs):
        if args[:3] == ("worktree", "remove", "--force"):
            return subprocess.CompletedProcess(list(args), 128, "", "index.lock")
        return original_out(cwd, *args, **kwargs)

    monkeypatch.setattr(hygiene, "_git_out", fake_git_out)
    monkeypatch.setattr(
        hygiene,
        "_registered_worktree_unlocked",
        lambda _checkout, _wt: True,
    )

    ok, _ = hygiene._try_remove_git_worktree(c.tree, c.repo)
    assert not ok
    assert c.tree.exists()
    assert agents.is_symlink()


def test_untracked_junction_path_is_not_blocking_force_allowlist(tmp_path, monkeypatch):
    from staff import worktree_links

    root = tmp_path / "wt"
    root.mkdir()
    junction = root / "junction"
    junction.mkdir()
    rel = "junction/inner.txt"
    (root / "junction" / "inner.txt").write_text("x")

    monkeypatch.setattr(
        hygiene,
        "_untracked_paths_for_safe_force_scan",
        lambda _wt: [rel],
    )
    monkeypatch.setattr(hygiene, "_safe_force_untracked_allow_prefixes", lambda _wt: frozenset())

    original = worktree_links._link_kind

    def kind(path, *args, **kwargs):
        if path == junction:
            return "Windows reparse point"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(hygiene, "_link_kind", kind)
    monkeypatch.setattr(Path, "is_symlink", lambda _p: False)

    assert not hygiene._untracked_outside_safe_force_allowlist(root)
