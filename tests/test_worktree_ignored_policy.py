"""Contracts for regenerable ignored-path allowlist (#4540)."""

from __future__ import annotations

import pytest
from worktree_ignored_policy import (
    IGNORED_FILES_REASON,
    blocking_ignored_paths_from_porcelain,
    ignored_files_removal_blocker,
    is_regenerable_ignored_path,
)


@pytest.mark.unit
def test_non_regenerable_ignored_paths_fail_closed() -> None:
    assert not is_regenerable_ignored_path(".env")
    assert not is_regenerable_ignored_path("config/local.yaml")
    porcelain = "!! .env\n!! __pycache__/x.pyc\n"
    assert blocking_ignored_paths_from_porcelain(porcelain) == [".env"]
    assert ignored_files_removal_blocker(porcelain) == IGNORED_FILES_REASON
    assert ignored_files_removal_blocker("!! __pycache__/a.pyc\n") == ""
