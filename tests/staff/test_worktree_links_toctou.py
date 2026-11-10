"""Fd-relative worktree link removal must not follow a swapped directory symlink."""

from __future__ import annotations

import os
import shutil

import pytest
from staff.worktree_links import unlink_worktree_links


def test_directory_swapped_for_symlink_before_open_does_not_touch_outside(tmp_path, monkeypatch):
    root = tmp_path / "wt"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim").write_text("keep")
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    (nested / "inner").write_text("inside")
    (root / "dispose-me").symlink_to("nested/inner")

    real_open = os.open

    def opening(name, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None and name == "nested" and nested.is_dir():
            shutil.rmtree(nested)
            nested.symlink_to(outside, target_is_directory=True)
        return real_open(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", opening)

    with pytest.raises(OSError):
        unlink_worktree_links(root)

    assert (outside / "victim").read_text() == "keep"


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
