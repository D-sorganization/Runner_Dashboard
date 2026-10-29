"""Regression test for #3389: a sparse checkout followed by a full checkout
in the same job leaves the tree sparse (skip-worktree bits survive the second
checkout), silently hiding files from later steps.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

_WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
_WORKFLOW_FILES = sorted(_WORKFLOWS_DIR.glob("*.yml")) + sorted(_WORKFLOWS_DIR.glob("*.yaml"))


def _is_checkout_step(step: dict) -> bool:
    uses = step.get("uses", "")
    return isinstance(uses, str) and uses.split("@", 1)[0] == "actions/checkout"


def _is_sparse(step: dict) -> bool:
    with_block = step.get("with") or {}
    return bool(with_block.get("sparse-checkout"))


def _job_checkout_kinds(job: dict) -> list[bool]:
    """Returns, in step order, True for a sparse checkout and False for a full one."""
    steps = job.get("steps") or []
    return [_is_sparse(step) for step in steps if _is_checkout_step(step)]


def _iter_jobs() -> Iterator[tuple[str, str, dict]]:
    for path in _WORKFLOW_FILES:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        jobs = (data or {}).get("jobs") or {}
        for job_name, job in jobs.items():
            if isinstance(job, dict):
                yield path.name, job_name, job


@pytest.mark.parametrize(
    ("workflow_name", "job_name", "job"),
    list(_iter_jobs()),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_no_full_checkout_after_sparse_checkout_in_same_job(workflow_name: str, job_name: str, job: dict) -> None:
    kinds = _job_checkout_kinds(job)
    for i, is_sparse in enumerate(kinds):
        if is_sparse:
            assert not any(not later for later in kinds[i + 1 :]), (
                f"{workflow_name}:{job_name} does a sparse actions/checkout "
                "followed by a full actions/checkout in the same job; the "
                "sparse skip-worktree bits survive the second checkout and "
                "silently hide files from later steps (#3389). Do the full "
                "checkout first, or drop the redundant checkout."
            )
