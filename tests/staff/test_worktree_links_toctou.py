"""Fd-relative worktree link removal must not follow a swapped directory symlink."""

from __future__ import annotations

import os
import shutil
import sys

import pytest
from staff.worktree_links import link_refusal_reason, unlink_worktree_links


def test_directory_swapped_for_symlink_before_open_does_not_touch_outside(tmp_path, monkeypatch):
    root = tmp_path / "wt"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim").write_text("keep")
    # Outside holds a same-named symlink during the unlink phase; must survive.
    (outside / "dispose-me").symlink_to("decoy", target_is_directory=False)
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    (nested / "inner").write_text("inside")
    (root / "dispose-me").symlink_to(outside / "victim")

    real_open = os.open
    swapped = False

    def opening(name, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if dir_fd is not None and name == "nested" and nested.is_dir():
            shutil.rmtree(nested)
            nested.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", opening)

    with pytest.raises(OSError):
        unlink_worktree_links(root)

    assert swapped
    assert (outside / "victim").read_text() == "keep"
    assert (outside / "dispose-me").is_symlink()


def test_same_named_outside_symlink_survives_successful_unlink(tmp_path):
    root = tmp_path / "wt"
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim"
    victim.write_text("keep")
    (outside / "trap-link").symlink_to("decoy")
    root.mkdir()
    (root / "trap-link").symlink_to(victim)

    unlink_worktree_links(root)

    assert not (root / "trap-link").exists()
    assert victim.read_text() == "keep"
    assert (outside / "trap-link").is_symlink()


def test_fd_relative_unlink_removes_only_link_entry(tmp_path):
    root = tmp_path / "wt"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "target.txt"
    target.write_text("payload")
    root.mkdir()
    link = root / "link"
    link.symlink_to(target)

    unlink_worktree_links(root)

    assert not link.exists()
    assert target.read_text() == "payload"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX RLIMIT_NOFILE and dir_fd walk")
def test_large_tree_under_low_nofile_does_not_emfile(tmp_path):
    import resource

    root = tmp_path / "wt"
    root.mkdir()
    venv = root / ".venv"
    lib = venv / "lib" / "python3.11" / "site-packages"
    lib.mkdir(parents=True)
    (lib / "module.py").write_text("#")
    (venv / "lib64").symlink_to("lib")
    wide = root / "wide"
    wide.mkdir()
    for i in range(400):
        (wide / f"pkg{i}").mkdir()

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, max(hard, 256)))
        assert link_refusal_reason(root, allow_in_tree=True) is None
        unlink_worktree_links(root)
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))

    assert not (venv / "lib64").exists()
    assert (lib / "module.py").is_file()
