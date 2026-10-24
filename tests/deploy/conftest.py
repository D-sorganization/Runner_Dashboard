"""Fixtures shared by the deploy tests."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

_CANDIDATE_BASES = ("/tmp", "/var/tmp")  # noqa: S108 - fixed, world-writable scratch roots outside any runner tree


def _inside_runner_work_tree(path: Path) -> bool:
    return "_work" in path.resolve().parts


def work_tree_free_base() -> Path:
    """A writable scratch directory that is not inside any runner ``_work`` tree.

    Self-hosted runners point ``TMPDIR`` (and so pytest's ``tmp_path``) at
    ``<runner>/_work/_tmp``. The runner hooks treat everything below the first
    ``_work`` component as that runner's tree, so a fixture that models "a path
    outside a runner work tree", or lays out sibling runners of its own, cannot
    live under ``tmp_path`` there: the hook correctly resolves the *real*
    runner instead of the fixture's.
    """
    for candidate in (Path(tempfile.gettempdir()), *map(Path, _CANDIDATE_BASES)):
        if candidate.is_dir() and not _inside_runner_work_tree(candidate):
            return candidate
    raise RuntimeError("no writable scratch directory outside a runner _work tree is available")


@pytest.fixture
def outside_work_tree_path() -> Iterator[Path]:
    """Per-test scratch directory guaranteed to sit outside any ``_work`` tree."""
    root = Path(tempfile.mkdtemp(prefix="rd-deploy-", dir=work_tree_free_base())).resolve()
    assert not _inside_runner_work_tree(root), root
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)
