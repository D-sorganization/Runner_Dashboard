"""Contract tests for merge_group pytest sharding helper."""

from __future__ import annotations

import pytest

from scripts.pytest_ci_shard import select_shard


def test_select_shard_splits_evenly() -> None:
    nodes = [f"tests/t_{i}.py::test_x" for i in range(6)]
    assert select_shard(nodes, 2, 1) == nodes[0::2]
    assert select_shard(nodes, 2, 2) == nodes[1::2]


def test_select_shard_single_group_returns_all() -> None:
    nodes = ["tests/a.py::test_one", "tests/b.py::test_two"]
    assert select_shard(nodes, 1, 1) == nodes


def test_select_shard_rejects_invalid_index() -> None:
    with pytest.raises(ValueError, match="index"):
        select_shard([], 2, 0)
    with pytest.raises(ValueError, match="index"):
        select_shard([], 2, 3)


def test_ci_standard_tests_matrix_two_shards_on_merge_group_only() -> None:
    from pathlib import Path

    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci-standard.yml").read_text(encoding="utf-8")
    )
    include = workflow["jobs"]["tests"]["strategy"]["matrix"]["include"]
    assert isinstance(include, str), "tests matrix must use a dynamic include expression"
    compact = include.replace(" ", "")
    assert "merge_group" in compact
    assert '"shards":2' in compact
    assert '"shard":1' in compact and '"shard":2' in compact
    assert '"shards":1' in compact


def test_tests_job_does_not_wait_on_lint() -> None:
    from pathlib import Path

    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci-standard.yml").read_text(encoding="utf-8")
    )
    needs = workflow["jobs"]["tests"]["needs"]
    assert "lint" not in needs
    assert "ci-health-check" in needs
