"""Regression coverage for #1521: staff tests must never touch real checkouts.

``staff.workspace.repos_roots()`` used to always append ``~/Repositories``,
``~/actions-runners/repos`` and ``/mnt/c/Users/$USERNAME/Repositories`` after
any configured ``STAFF_REPOS_ROOT`` override, so a test session on a
developer box could discover real git checkouts and run real ``git worktree
add`` / ``gh`` calls against them. These tests assert the autouse isolation
fixture in ``tests/conftest.py`` neutralizes that discovery for the whole
suite.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from staff import workspace


@pytest.mark.unit
def test_repos_roots_is_empty_inside_the_test_session() -> None:
    """Inside pytest, repos_roots() must never resolve a real checkout root.

    Before the fix this returns ``~/Repositories`` (and friends) whenever
    they exist on the host, which is exactly the leak reported in #1521.
    """
    assert workspace.repos_roots() == []


@pytest.mark.unit
def test_staff_worktrees_root_is_under_tmp_path(tmp_path: Path) -> None:
    """staff_worktrees_root() must be isolated per-test, never a real checkout."""
    root = workspace.staff_worktrees_root()
    # The autouse fixture points STAFF_WORKTREES_ROOT at a subdirectory of
    # this test's own tmp_path.
    assert root.resolve() == (tmp_path / "staff-worktrees").resolve()


@pytest.mark.unit
def test_add_worktree_guard_rejects_targets_outside_tmp_path() -> None:
    """DbC guard: add_worktree() must refuse to create a worktree outside tmp.

    This is the regression test called for by the issue: fail loudly if a
    test-created run ever resolves a checkout/worktree path outside the
    pytest tmp root, rather than silently shelling out to real git.
    """
    outside_checkout = Path(tempfile.gettempdir()) / "staff-1521-guard-checkout"
    outside_worktree = Path(tempfile.gettempdir()) / "staff-1521-guard-worktree"

    with pytest.raises(AssertionError):
        workspace.add_worktree(outside_checkout, outside_worktree, "staff/should-never-be-created")

    # The guard must fire before any filesystem/subprocess work happens.
    assert not outside_worktree.exists()
