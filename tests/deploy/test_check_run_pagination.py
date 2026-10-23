"""A deployment must never qualify a truncated or wrong-commit CI inventory."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy"))
from node_auto_deploy.ci_gate import ci_gate_for_deploy_commit  # noqa: E402
from node_auto_deploy.runner import CycleDeps, _check_runs_for_commit  # noqa: E402
from node_auto_deploy.state import AutoDeployState  # noqa: E402

SHA = "a" * 40


def _run(identifier: int, conclusion: str = "success") -> dict:
    return {
        "id": identifier,
        "head_sha": SHA,
        "name": f"check-{identifier}",
        "status": "completed",
        "conclusion": conclusion,
    }


def _fetch(get):
    deps = CycleDeps(get, lambda: [], lambda: {}, lambda: 0, AutoDeployState())
    return _check_runs_for_commit(deps, "owner/repo", SHA)


def test_failure_on_second_page_blocks_deployment() -> None:
    paths = []

    def get(path):
        paths.append(path)
        runs = [_run(i) for i in range(100)] if "page=2" not in path else [_run(100, "failure")]
        return {"total_count": 101, "check_runs": runs}

    checks = _fetch(get)
    assert len(checks) == 101
    assert len(paths) == 2
    assert not ci_gate_for_deploy_commit(checks, SHA)[0]


@pytest.mark.parametrize(
    "mode",
    ["missing-total", "short-page", "changing-total", "duplicate", "wrong-sha", "missing-sha", "malformed", "too-many"],
)
def test_incomplete_inventory_is_rejected(mode: str) -> None:
    def get(path):
        second = "page=2" in path
        runs = [_run(i) for i in range(100)] if not second else [_run(100)]
        total = 101
        if mode == "missing-total":
            return {"check_runs": runs}
        if mode == "short-page":
            runs = runs[:1]
        if mode == "changing-total" and second:
            total = 102
        if mode == "duplicate" and second:
            runs = [_run(0)]
        if mode == "wrong-sha":
            runs[0]["head_sha"] = "b" * 40
        if mode == "missing-sha":
            runs[0].pop("head_sha")
        if mode == "malformed":
            runs[0] = "not a check"
        if mode == "too-many":
            total = 5001
        return {"total_count": total, "check_runs": runs}

    with pytest.raises(RuntimeError, match="check"):
        _fetch(get)


def test_complete_inventory_does_not_fetch_extra_page() -> None:
    paths = []

    def get(path):
        paths.append(path)
        return {"total_count": 100, "check_runs": [_run(i) for i in range(100)]}

    assert len(_fetch(get)) == 100
    assert len(paths) == 1
