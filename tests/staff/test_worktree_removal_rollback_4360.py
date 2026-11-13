"""Regression: failed staff worktree removal restores all tracked symlinks (#4360)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from staff import worktree_hygiene as hygiene
from staff import worktree_links


def _git(cwd: Path, *args: str) -> None:
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    subprocess.run(
        [
            "git",
            "-C",
            str(cwd),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@users.noreply.github.com",
            *args,
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )


@pytest.fixture
def tracked_symlink_worktree(tmp_path: Path):
    root = Path(tempfile.mkdtemp(dir=tmp_path, prefix="rd4360-"))
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    sentinel = root / "external-sentinel"
    sentinel.write_text("outside worktree\n")
    links = (".gaai/agents", "docs/model")
    for rel in links:
        link = repo / rel
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(sentinel)
    _git(repo, "add", "--", *links)
    _git(repo, "commit", "-qm", "tracked symlink fixture")
    tree = root / "worktree"
    _git(repo, "worktree", "add", "-q", "--detach", str(tree))
    assert hygiene._eligible_for_safe_force_removal(tree)
    return {"root": root, "repo": repo, "tree": tree, "sentinel": sentinel, "links": links}


@pytest.mark.parametrize("failure", ["nonzero", "timeout", "partial-unlink"])
def test_failed_removal_restores_tracked_symlinks(tracked_symlink_worktree, failure: str) -> None:
    repo = tracked_symlink_worktree["repo"]
    tree = tracked_symlink_worktree["tree"]
    sentinel = tracked_symlink_worktree["sentinel"]
    links = tracked_symlink_worktree["links"]
    root = tracked_symlink_worktree["root"]

    git_out = hygiene._git_out
    unlink_entries = hygiene.unlink_worktree_links

    def inject_git(cwd: Path, *args: str, timeout: int = 30):
        if args[:2] == ("worktree", "remove"):
            if failure == "timeout":
                raise subprocess.TimeoutExpired(list(args), timeout)
            return subprocess.CompletedProcess(list(args), 128, "", "injected")
        if args[:2] == ("worktree", "prune"):
            return subprocess.CompletedProcess(list(args), 0, "", "")
        return git_out(cwd, *args, timeout=timeout)

    def inject_unlink(path: Path, *args, **kwargs):
        if failure == "partial-unlink":
            (path / links[0]).rename(root / "saved-fixture-link")
            raise OSError("injected after the first link was removed")
        return unlink_entries(path, *args, **kwargs)

    with (
        patch.object(hygiene, "_git_out", side_effect=inject_git),
        patch.object(hygiene, "unlink_worktree_links", side_effect=inject_unlink),
    ):
        try:
            removed, _ = hygiene._try_remove_git_worktree(tree, repo)
            assert not removed
        except (OSError, subprocess.TimeoutExpired):
            pass

    assert sentinel.read_text() == "outside worktree\n"
    surviving = [rel for rel in links if (tree / rel).is_symlink()]
    assert surviving == list(links)
    assert hygiene._worktree_tracked_clean_vs_head(tree)
    assert hygiene._eligible_for_safe_force_removal(tree)


def test_prune_timeout_after_failed_remove_still_allows_retry(tracked_symlink_worktree) -> None:
    repo, tree = tracked_symlink_worktree["repo"], tracked_symlink_worktree["tree"]
    link = tree / ".gaai/agents"
    git_out = hygiene._git_out

    def inject(cwd: Path, *args: str, timeout: int = 30):
        if args[:2] == ("worktree", "remove"):
            return subprocess.CompletedProcess(list(args), 128, "", "injected")
        if args[:2] == ("worktree", "prune"):
            raise subprocess.TimeoutExpired(list(args), timeout)
        return git_out(cwd, *args, timeout=timeout)

    with patch.object(hygiene, "_git_out", side_effect=inject):
        try:
            removed, _ = hygiene._try_remove_git_worktree(tree, repo)
            assert not removed
        except subprocess.TimeoutExpired:
            pass
    assert hygiene._eligible_for_safe_force_removal(tree)
    assert os.readlink(link) == str(tracked_symlink_worktree["sentinel"])


def test_interrupt_after_failed_remove_restores_retry_state(tracked_symlink_worktree) -> None:
    repo, tree = tracked_symlink_worktree["repo"], tracked_symlink_worktree["tree"]
    git_out = hygiene._git_out

    def inject(cwd: Path, *args: str, timeout: int = 30):
        if args[:2] == ("worktree", "remove"):
            raise KeyboardInterrupt("simulated interruption")
        return git_out(cwd, *args, timeout=timeout)

    with patch.object(hygiene, "_git_out", side_effect=inject), pytest.raises(KeyboardInterrupt):
        hygiene._try_remove_git_worktree(tree, repo)
    assert hygiene._eligible_for_safe_force_removal(tree)
    assert (tree / ".gaai/agents").is_symlink()


def test_concurrent_replacement_is_not_overwritten_by_rollback(tracked_symlink_worktree) -> None:
    repo = tracked_symlink_worktree["repo"]
    tree = tracked_symlink_worktree["tree"]
    link = tree / ".gaai/agents"
    replacement = tracked_symlink_worktree["root"] / "not-present"
    git_out = hygiene._git_out

    def inject(cwd: Path, *args: str, timeout: int = 30):
        if args[:2] == ("worktree", "remove"):
            link.symlink_to(replacement)
            return subprocess.CompletedProcess(list(args), 128, "", "injected")
        if args[:2] == ("worktree", "prune"):
            return subprocess.CompletedProcess(list(args), 0, "", "")
        return git_out(cwd, *args, timeout=timeout)

    with patch.object(hygiene, "_git_out", side_effect=inject):
        removed, _ = hygiene._try_remove_git_worktree(tree, repo)
    assert not removed
    assert os.readlink(link) == str(replacement)
    assert not hygiene._eligible_for_safe_force_removal(tree)


def test_second_sweep_succeeds_after_rollback(tracked_symlink_worktree) -> None:
    repo = tracked_symlink_worktree["repo"]
    tree = tracked_symlink_worktree["tree"]
    calls = {"remove": 0}
    git_out = hygiene._git_out

    def inject(cwd: Path, *args: str, timeout: int = 30):
        if args[:2] == ("worktree", "remove"):
            calls["remove"] += 1
            if calls["remove"] == 1:
                return subprocess.CompletedProcess(list(args), 128, "", "transient")
            import shutil

            shutil.rmtree(tree, ignore_errors=True)
            return subprocess.CompletedProcess(list(args), 0, "", "")
        if args[:2] == ("worktree", "prune"):
            return subprocess.CompletedProcess(list(args), 0, "", "")
        return git_out(cwd, *args, timeout=timeout)

    with patch.object(hygiene, "_git_out", side_effect=inject):
        ok1, _ = hygiene._try_remove_git_worktree(tree, repo)
        assert not ok1
        assert tree.exists()
        assert hygiene._eligible_for_safe_force_removal(tree)
        ok2, _ = hygiene._try_remove_git_worktree(tree, repo)
    assert ok2
    assert not tree.exists()


def test_rollback_create_failure_is_reported(tracked_symlink_worktree, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, tree = tracked_symlink_worktree["repo"], tracked_symlink_worktree["tree"]
    git_out = hygiene._git_out
    real_symlink = os.symlink

    def inject(cwd: Path, *args: str, timeout: int = 30):
        if args[:2] == ("worktree", "remove"):
            return subprocess.CompletedProcess(list(args), 128, "", "injected")
        if args[:2] == ("worktree", "prune"):
            return subprocess.CompletedProcess(list(args), 0, "", "")
        return git_out(cwd, *args, timeout=timeout)

    def fail_symlink(src, dst, *args, **kwargs):
        if Path(dst).name == "model":
            raise OSError("simulated rollback create failure")
        return real_symlink(src, dst, *args, **kwargs)

    with patch.object(hygiene, "_git_out", side_effect=inject), patch.object(os, "symlink", fail_symlink):
        removed, _ = hygiene._try_remove_git_worktree(tree, repo)
    assert not removed
    assert (tree / ".gaai/agents").is_symlink()
    assert not (tree / "docs/model").exists()


def test_rollback_refuses_path_based_create_without_dir_fd_support(
    tracked_symlink_worktree, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """No descriptor-relative create (as on Windows): nothing is created by path; each link is reported."""
    repo, tree = tracked_symlink_worktree["repo"], tracked_symlink_worktree["tree"]
    created: list[object] = []
    monkeypatch.setattr(hygiene, "_DIR_FD_LINKS", False)
    monkeypatch.setattr(os, "symlink", lambda *args, **kwargs: created.append(args))
    monkeypatch.setattr(Path, "symlink_to", lambda *args, **kwargs: created.append(args))
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: created.append(args))

    def fail_remove(args, _timeout):
        return subprocess.CompletedProcess(args, 128, "", "injected")

    with caplog.at_level("WARNING"), _inject_remove(tracked_symlink_worktree, fail_remove):
        removed, _ = hygiene._try_remove_git_worktree(tree, repo)

    assert not removed
    assert created == []
    for rel in tracked_symlink_worktree["links"]:
        assert not (tree / rel).is_symlink()
        assert f"rollback failed for {rel}" in caplog.text
    assert "open to a parent swap" in caplog.text


def test_junction_snapshot_is_refused_not_created_by_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A junction only exists where links cannot be created relative to an open directory."""
    root = tmp_path / "wt"
    root.mkdir()
    snap = hygiene._TrackedSymlinkSnapshot(
        rel_posix="deps/junction", kind="junction", target=str(tmp_path), target_is_directory=True
    )
    calls: list[object] = []
    monkeypatch.setattr(hygiene, "_create_windows_junction", lambda *args: calls.append(args))

    with pytest.raises(OSError, match="not restored"):
        hygiene._recreate_tracked_link_under_real_parents(root, snap)

    assert calls == []
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("list_outcome", ["nonzero", "locked"])
def test_rollback_that_cannot_confirm_the_worktree_is_reported(
    tracked_symlink_worktree, caplog: pytest.LogCaptureFixture, list_outcome: str
) -> None:
    """A rollback skipped because the tree is unconfirmed or locked must say which links it left."""
    repo, tree = tracked_symlink_worktree["repo"], tracked_symlink_worktree["tree"]
    git_out = hygiene._git_out
    removal_attempted = False

    def inject(cwd: Path, *args: str, timeout: int = 30):
        nonlocal removal_attempted
        if args[:2] == ("worktree", "remove"):
            removal_attempted = True
            return subprocess.CompletedProcess(list(args), 128, "", "injected")
        if args[:2] == ("worktree", "prune"):
            return subprocess.CompletedProcess(list(args), 0, "", "")
        if args[:2] == ("worktree", "list") and removal_attempted:
            if list_outcome == "nonzero":
                return subprocess.CompletedProcess(list(args), 1, "", "injected list failure")
            real = git_out(cwd, *args, timeout=timeout)
            return subprocess.CompletedProcess(list(args), 0, real.stdout.replace("\0\0", "\0locked\0\0"), "")
        return git_out(cwd, *args, timeout=timeout)

    with (
        caplog.at_level("WARNING", logger=hygiene.log.name),
        patch.object(hygiene, "_git_out", side_effect=inject),
    ):
        removed, _ = hygiene._try_remove_git_worktree(tree, repo)

    assert not removed
    assert "rollback skipped" in caplog.text
    for rel in tracked_symlink_worktree["links"]:
        assert rel in caplog.text, f"the unrestored link {rel} must be named"


@pytest.fixture
def tracked_directory_symlink_worktree(tmp_path: Path):
    root = Path(tempfile.mkdtemp(dir=tmp_path, prefix="rd4360-dir-"))
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    outside = root / "external-dir"
    outside.mkdir()
    (outside / "sentinel").write_text("dir target\n")
    rel = ".gaai/agents"
    link = repo / rel
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    _git(repo, "add", "--", rel)
    _git(repo, "commit", "-qm", "directory symlink fixture")
    tree = root / "worktree"
    _git(repo, "worktree", "add", "-q", "--detach", str(tree))
    return {"root": root, "repo": repo, "tree": tree, "outside": outside, "rel": rel}


def test_rollback_restores_directory_symlink_semantics(tracked_directory_symlink_worktree) -> None:
    repo = tracked_directory_symlink_worktree["repo"]
    tree = tracked_directory_symlink_worktree["tree"]
    rel = tracked_directory_symlink_worktree["rel"]
    outside = tracked_directory_symlink_worktree["outside"]
    link = tree / rel
    journal = hygiene._capture_tracked_symlink_journal(tree)
    assert journal[0].kind == "symlink"
    assert journal[0].target_is_directory
    link.unlink()
    assert not link.exists()
    recreated: list[bool] = []
    real_symlink = os.symlink

    def record_directory_semantics(src, dst, target_is_directory=False, **kwargs):
        recreated.append(bool(target_is_directory))
        return real_symlink(src, dst, target_is_directory, **kwargs)

    with patch.object(os, "symlink", record_directory_semantics):
        failures = hygiene._restore_tracked_symlinks_from_journal(tree, journal, repo)
    assert failures == []
    assert link.is_symlink()
    assert link.is_dir()
    assert recreated == [True]
    assert (outside / "sentinel").read_text() == "dir target\n"


def test_rollback_restores_file_symlink_without_directory_flag(tracked_symlink_worktree) -> None:
    tree = tracked_symlink_worktree["tree"]
    repo = tracked_symlink_worktree["repo"]
    rel = "docs/model"
    link = tree / rel
    journal = hygiene._capture_tracked_symlink_journal(tree)
    file_snap = next(s for s in journal if s.rel_posix == rel)
    assert file_snap.kind == "symlink"
    assert not file_snap.target_is_directory
    link.unlink()
    directory_flags: list[bool] = []
    real_symlink = os.symlink

    def record(src, dst, target_is_directory=False, **kwargs):
        directory_flags.append(bool(target_is_directory))
        return real_symlink(src, dst, target_is_directory, **kwargs)

    with patch.object(os, "symlink", record):
        failures = hygiene._restore_tracked_symlinks_from_journal(tree, journal, repo)
    assert failures == []
    assert link.is_symlink()
    assert directory_flags == [False]


def test_rollback_restores_windows_junction_kind(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "wt"
    root.mkdir()
    entry = root / "junction"
    target = tmp_path / "outside"
    target.mkdir()
    snap = hygiene._TrackedSymlinkSnapshot(
        rel_posix="junction",
        kind="junction",
        target=str(target),
        target_is_directory=True,
    )
    junction_calls: list[tuple[Path, str]] = []
    symlink_calls: list[Path] = []

    def fake_junction(link: Path, junction_target: str) -> None:
        junction_calls.append((link, junction_target))
        entry.mkdir(exist_ok=True)

    def fake_symlink_to(self, *_args, **_kwargs):
        symlink_calls.append(self)

    monkeypatch.setattr(hygiene, "_create_windows_junction", fake_junction)
    with patch.object(Path, "symlink_to", fake_symlink_to):
        hygiene._recreate_tracked_link_entry(entry, snap)
    assert junction_calls == [(entry, str(target))]
    assert symlink_calls == []


def test_capture_classifies_windows_junction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = tmp_path / "agents"
    target = tmp_path / "outside"
    target.mkdir()
    monkeypatch.setattr(hygiene, "_is_junction_path", lambda _path: True)
    kind, target_is_directory = hygiene._classify_tracked_link(entry, "Windows reparse point")
    assert kind == "junction"
    assert target_is_directory


def test_capture_classifies_directory_symlink_reparse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = tmp_path / "agents"
    monkeypatch.setattr(hygiene, "_is_junction_path", lambda _path: False)
    original_lstat = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == entry:
            return type("Stat", (), {"st_file_attributes": 0x410})()
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    kind, target_is_directory = hygiene._classify_tracked_link(entry, "Windows reparse point")
    assert kind == "symlink"
    assert target_is_directory


def test_recreate_junction_failure_surfaces_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = tmp_path / "junction"
    target = tmp_path / "outside"
    target.mkdir()
    snap = hygiene._TrackedSymlinkSnapshot(
        rel_posix="junction",
        kind="junction",
        target=str(target),
        target_is_directory=True,
    )

    def fail_junction(_link: Path, _target: str) -> None:
        raise OSError("simulated junction create failure")

    monkeypatch.setattr(hygiene, "_create_windows_junction", fail_junction)
    with pytest.raises(OSError, match="simulated junction create failure"):
        hygiene._recreate_tracked_link_entry(entry, snap)
    assert not entry.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows junction primitives")
def test_windows_junction_round_trip_real(tmp_path: Path) -> None:
    target = tmp_path / "outside"
    target.mkdir()
    (target / "sentinel").write_text("junction evidence\n")
    entry = tmp_path / "junction"
    hygiene._create_windows_junction(entry, str(target))
    assert worktree_links._is_junction_path(entry)
    assert os.readlink(entry) == str(target)
    snap = hygiene._TrackedSymlinkSnapshot(
        rel_posix="junction",
        kind="junction",
        target=str(target),
        target_is_directory=True,
    )
    entry.rmdir()
    hygiene._recreate_tracked_link_entry(entry, snap)
    assert worktree_links._is_junction_path(entry)
    assert (target / "sentinel").read_text() == "junction evidence\n"


def _inject_remove(tracked, on_remove):
    """Patch ``_git_out`` so only ``worktree remove`` is intercepted; prune is a no-op."""
    git_out = hygiene._git_out

    def inject(cwd: Path, *args: str, timeout: int = 30):
        if args[:2] == ("worktree", "remove"):
            return on_remove(list(args), timeout)
        if args[:2] == ("worktree", "prune"):
            return subprocess.CompletedProcess(list(args), 0, "", "")
        return git_out(cwd, *args, timeout=timeout)

    return patch.object(hygiene, "_git_out", side_effect=inject)


def test_unexpected_unlink_error_still_restores_removed_links(tracked_symlink_worktree) -> None:
    """Rollback is unconditional: an error type nobody anticipated still restores (#4360)."""
    repo, tree = tracked_symlink_worktree["repo"], tracked_symlink_worktree["tree"]
    links = tracked_symlink_worktree["links"]

    def partial_then_unexpected(path: Path, *args, **kwargs):
        (path / links[0]).unlink()
        raise RuntimeError("injected non-OSError after the first link was removed")

    with (
        patch.object(hygiene, "unlink_worktree_links", side_effect=partial_then_unexpected),
        pytest.raises(RuntimeError, match="injected non-OSError"),
    ):
        hygiene._try_remove_git_worktree(tree, repo)

    assert [rel for rel in links if (tree / rel).is_symlink()] == list(links)
    assert tracked_symlink_worktree["sentinel"].read_text() == "outside worktree\n"
    assert hygiene._eligible_for_safe_force_removal(tree)


def test_rollback_refresh_timeout_does_not_mask_removal_result(tracked_symlink_worktree) -> None:
    """A failing rollback step is logged; the original exit 128 is still what the caller sees."""
    repo, tree = tracked_symlink_worktree["repo"], tracked_symlink_worktree["tree"]
    links = tracked_symlink_worktree["links"]
    git_in_worktree = hygiene._git_in_worktree

    def refresh_times_out(worktree: Path, *args: str, timeout: int = 30):
        if args[:1] == ("update-index",):
            raise subprocess.TimeoutExpired(list(args), timeout)
        return git_in_worktree(worktree, *args, timeout=timeout)

    with (
        _inject_remove(tracked_symlink_worktree, lambda args, _t: subprocess.CompletedProcess(args, 128, "", "boom")),
        patch.object(hygiene, "_git_in_worktree", side_effect=refresh_times_out),
    ):
        removed, result = hygiene._try_remove_git_worktree(tree, repo)

    assert not removed
    assert (result.returncode, result.stderr) == (128, "boom")
    assert [rel for rel in links if (tree / rel).is_symlink()] == list(links)
    # The skipped refresh leaves the index stat-dirty only; the content matches HEAD.
    status = subprocess.run(
        ["git", "-C", str(tree), "status", "--porcelain"], capture_output=True, text=True, check=True, timeout=20
    )
    assert status.stdout == ""
    assert hygiene._eligible_for_safe_force_removal(tree)


def test_rollback_registration_timeout_does_not_mask_remove_timeout(
    tracked_symlink_worktree, caplog: pytest.LogCaptureFixture
) -> None:
    """Unverifiable registration skips restoration, is reported, and keeps the original error."""
    repo, tree = tracked_symlink_worktree["repo"], tracked_symlink_worktree["tree"]
    git_out = hygiene._git_out
    listed = {"count": 0}

    def inject(cwd: Path, *args: str, timeout: int = 30):
        if args[:2] == ("worktree", "remove"):
            raise subprocess.TimeoutExpired(["worktree", "remove"], timeout)
        if args[:2] == ("worktree", "list"):
            listed["count"] += 1
            if listed["count"] > 1:  # the pre-unlink check passes; the rollback check hangs
                raise subprocess.TimeoutExpired(["worktree", "list"], timeout)
        return git_out(cwd, *args, timeout=timeout)

    with (
        patch.object(hygiene, "_git_out", side_effect=inject),
        caplog.at_level("WARNING", logger="dashboard.staff.worktree_hygiene"),
        pytest.raises(subprocess.TimeoutExpired) as raised,
    ):
        hygiene._try_remove_git_worktree(tree, repo)

    assert raised.value.cmd == ["worktree", "remove"]
    assert any("rollback" in rec.getMessage() and str(tree) in rec.getMessage() for rec in caplog.records)
    assert tracked_symlink_worktree["sentinel"].read_text() == "outside worktree\n"


def test_prune_timeout_cannot_turn_confirmed_removal_into_failure(tracked_symlink_worktree) -> None:
    repo, tree = tracked_symlink_worktree["repo"], tracked_symlink_worktree["tree"]
    git_out = hygiene._git_out

    def inject(cwd: Path, *args: str, timeout: int = 30):
        if args[:2] == ("worktree", "prune"):
            raise subprocess.TimeoutExpired(list(args), timeout)
        return git_out(cwd, *args, timeout=timeout)  # real removal of the scratch fixture

    with patch.object(hygiene, "_git_out", side_effect=inject):
        removed, result = hygiene._try_remove_git_worktree(tree, repo)

    assert removed
    assert result.returncode == 0
    assert not tree.exists()
    assert tracked_symlink_worktree["sentinel"].read_text() == "outside worktree\n"


def test_rollback_never_writes_through_a_replaced_parent(tracked_symlink_worktree) -> None:
    """A parent swapped for a link mid-attempt is preserved; nothing is created outside the tree."""
    repo, tree = tracked_symlink_worktree["repo"], tracked_symlink_worktree["tree"]
    outside = tracked_symlink_worktree["root"] / "external-dir"
    outside.mkdir()

    def swap_parent(args, _timeout):
        (tree / "docs").rmdir()  # empty once its tracked link was unlinked
        (tree / "docs").symlink_to(outside, target_is_directory=True)
        return subprocess.CompletedProcess(args, 128, "", "injected")

    with _inject_remove(tracked_symlink_worktree, swap_parent):
        removed, _ = hygiene._try_remove_git_worktree(tree, repo)

    assert not removed
    assert list(outside.iterdir()) == []
    assert os.readlink(tree / "docs") == str(outside)
    assert (tree / ".gaai/agents").is_symlink()
    assert tracked_symlink_worktree["sentinel"].read_text() == "outside worktree\n"


@pytest.mark.skipif(os.symlink not in os.supports_dir_fd, reason="needs dir_fd-relative symlink creation")
def test_rollback_survives_parent_swap_between_check_and_create(tracked_symlink_worktree) -> None:
    """A parent swapped for a link after it was checked must not redirect the restored link (#4400 review)."""
    repo, tree = tracked_symlink_worktree["repo"], tracked_symlink_worktree["tree"]
    outside = tracked_symlink_worktree["root"] / "external-dir"
    outside.mkdir()
    real_symlink = os.symlink
    swapped: list[str] = []

    def swap_parent_then_create(src, dst, *args, **kwargs):
        if Path(dst).name == "model" and not swapped:
            swapped.append(str(dst))
            (tree / "docs").rename(tree / "docs-checked")
            real_symlink(outside, tree / "docs", True)
        return real_symlink(src, dst, *args, **kwargs)

    failed = subprocess.CompletedProcess([], 128, "", "injected")
    with (
        _inject_remove(tracked_symlink_worktree, lambda _a, _t: failed),
        patch.object(os, "symlink", swap_parent_then_create),
    ):
        removed, _ = hygiene._try_remove_git_worktree(tree, repo)

    assert not removed
    assert swapped, "the swap hook never ran"
    assert list(outside.iterdir()) == []
    assert os.readlink(tree / "docs-checked" / "model") == str(tracked_symlink_worktree["sentinel"])


@pytest.mark.skipif(os.symlink not in os.supports_dir_fd, reason="needs dir_fd-relative symlink creation")
def test_rollback_never_writes_through_a_replaced_worktree_root(tracked_symlink_worktree) -> None:
    """The worktree directory itself swapped for a link: rollback creates nothing behind it."""
    repo, tree = tracked_symlink_worktree["repo"], tracked_symlink_worktree["tree"]
    outside = tracked_symlink_worktree["root"] / "external-dir"
    outside.mkdir()

    def swap_root(args, _timeout):
        tree.rename(tree.with_name("worktree-moved"))
        tree.symlink_to(outside, target_is_directory=True)
        return subprocess.CompletedProcess(args, 128, "", "injected")

    with _inject_remove(tracked_symlink_worktree, swap_root):
        removed, _ = hygiene._try_remove_git_worktree(tree, repo)

    assert not removed
    assert list(outside.iterdir()) == []
